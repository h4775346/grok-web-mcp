"""Grok Web MCP: MCP server (stdio) + WebSocket hub on 127.0.0.1 for the companion Chrome extension.

The extension runs the jobs inside the user's own logged-in grok.com/imagine tab and sends the results back as
base64; this server saves them to disk and returns the paths. No xAI API key is involved.

Only one process can own the port. The first grok-web-mcp process is the "hub"; a later one (another MCP client
session) becomes a "peer" that forwards its jobs to the hub, so jobs from every session share one queue.

Auth (loopback only):
  * sockets from web pages (http/https Origin) are always refused;
  * the Chrome extension is recognised by its chrome-extension://<id> Origin. The first extension id that connects
    is pinned in extensions.json (trust on first use); any other extension id is refused until it is added there
    or to GROK_WEB_MCP_EXTENSION_IDS;
  * other local clients (a second grok-web-mcp session) present the random token from token.txt, read automatically
    by both sessions. Optional: require_peer_token=false skips it. The extension never needs it.

Settings: see settings.py (config.json + GROK_WEB_MCP_* environment variables).

Quota guard: every Grok submission uses the user's Imagine quota, so the hub keeps a per-day ledger (usage.json,
written only by the hub) and refuses jobs over the limits in limits.json.
"""

import asyncio
import base64
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from . import __version__, settings
from .settings import ASPECTS, CONFIG_FILE, HOME, MODES

try:
    S, SOURCES = settings.load()
except settings.SettingsError as _e:
    sys.exit(f"[grok-web-mcp] invalid setting: {_e}")

HOST = "127.0.0.1"  # never configurable: the bridge is loopback only
PORT = S["port"]
OUTPUT = Path(S["output_dir"]).expanduser()
TOKEN_FILE = HOME / "token.txt"
USAGE_FILE = HOME / "usage.json"
LIMITS_FILE = HOME / "limits.json"
EXTENSIONS_FILE = HOME / "extensions.json"
DEFAULT_LIMITS = {"max_image_submissions_per_day": 30, "max_videos_per_day": 5, "max_jobs_per_batch": 12, "confirm_over": 10}
PER_SUBMIT = 2  # one Grok image submission returns 2 images
EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}
MAX_MSG = 256 * 2**20  # videos arrive as base64
NO_EXTENSION = (
    "no extension connected: open Google Chrome with the Grok Web MCP extension loaded "
    "(chrome://extensions > Load unpacked) and retry"
)


def log(*a):
    print("[grok-web-mcp]", *a, file=sys.stderr, flush=True)  # stdout belongs to the MCP protocol


def load_token() -> str | None:
    """The peer token, created on first use. None when require_peer_token is off and there is no token file."""
    if not S["require_peer_token"]:
        return (TOKEN_FILE.read_text(encoding="utf-8").strip() or None) if TOKEN_FILE.exists() else None
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    log(f"created a new token file: {TOKEN_FILE}")
    return t


class BridgeError(Exception):
    submitted = False  # True when the prompt reached Grok before the failure (it still used quota)


# -------------------------------------------------------------------- quota ledger + limits
def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def load_limits() -> dict:
    raw = _read_json(LIMITS_FILE)
    if not isinstance(raw, dict):
        if not LIMITS_FILE.exists():
            _write_json(LIMITS_FILE, DEFAULT_LIMITS)
        raw = {}
    return {**DEFAULT_LIMITS, **{k: int(v) for k, v in raw.items() if k in DEFAULT_LIMITS}}


def _today() -> str:
    return time.strftime("%Y-%m-%d")  # local day


def usage_today() -> dict:
    day = (_read_json(USAGE_FILE) or {}).get(_today()) or {}
    return {k: int(day.get(k, 0)) for k in ("image_submissions", "images_generated", "images_kept", "videos")}


def record_usage(kind: str, generated: int = 0, kept: int = 0):
    """Only the hub calls this, under its job lock, after the extension reported a real submission."""
    day = usage_today()
    if kind == "image":
        day["image_submissions"] += 1
        day["images_generated"] += generated
        day["images_kept"] += kept
    else:
        day["videos"] += 1
    ledger = _read_json(USAGE_FILE) or {}
    ledger[_today()] = day
    _write_json(USAGE_FILE, dict(sorted(ledger.items())[-90:]))  # keep ~3 months


def check_budget(images: int = 0, videos: int = 0):
    if not S["quota_guard"]:
        return
    lim, u = load_limits(), usage_today()
    for need, used_k, lim_k, what in (
        (images, "image_submissions", "max_image_submissions_per_day", "image submission"),
        (videos, "videos", "max_videos_per_day", "video generation"),
    ):
        if need and u[used_k] + need > lim[lim_k]:
            raise BridgeError(
                f"Grok budget: this needs {need} {what}(s) but {u[used_k]}/{lim[lim_k]} are already used "
                f"today ({_today()}), so nothing was submitted. To allow more, ask the user, then call "
                f"grok_set_limits({lim_k}=N) or edit {LIMITS_FILE}."
            )


# -------------------------------------------------------------------- extension pinning (trust on first use)
def allowed_extensions() -> set[str]:
    ids = set(S["extension_ids"])
    raw = _read_json(EXTENSIONS_FILE)
    if isinstance(raw, dict):
        ids |= {str(i) for i in raw.get("allowed", [])}
    return ids


def extension_allowed(ext_id: str) -> bool:
    if not S["pin_extension"]:
        return True
    ids = allowed_extensions()
    if not ids:
        _write_json(
            EXTENSIONS_FILE,
            {
                "allowed": [ext_id],
                "note": "extension ids allowed to run jobs; the first one that connected was pinned automatically",
            },
        )
        log(f"pinned Chrome extension {ext_id} (trust on first use, see {EXTENSIONS_FILE})")
        return True
    return ext_id in ids


class Hub:
    def __init__(self, token: str | None):
        self.token = token
        self.role = None  # "hub" | "peer"
        self.server = None
        self.ext = None  # websocket of the connected extension
        self.ext_info = {}
        self.pending = {}  # job id -> Future
        self.lock = asyncio.Lock()  # one job at a time
        self.queued = 0

    async def start(self):
        try:
            self.server = await serve(self._handler, HOST, PORT, max_size=MAX_MSG, ping_interval=20, ping_timeout=60)
            self.role = "hub"
            log(f"hub listening on ws://{HOST}:{PORT}")
        except OSError:
            self.role = "peer"
            log(f"port {PORT} is busy: running as a peer of the existing grok-web-mcp hub")

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    # ---------------------------------------------------------------- websocket side
    async def _handler(self, ws):
        origin = ws.request.headers.get("Origin") or ""
        if origin.startswith(("http://", "https://")):  # web pages may never talk to the bridge
            await ws.close(4003, "origin not allowed")
            return
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        except Exception:
            await ws.close(4000, "expected hello")
            return
        if origin.startswith("chrome-extension://"):
            ext_id = origin.removeprefix("chrome-extension://").strip("/")
            if hello.get("role") != "extension" or not extension_allowed(ext_id):
                await ws.send(
                    json.dumps({"type": "error", "error": f"extension {ext_id} is not allowed; add its id to {EXTENSIONS_FILE}"})
                )
                await ws.close(4004, "extension not allowed")
                return
        elif hello.get("role") == "extension":  # only a real chrome-extension origin may take the extension role
            await ws.close(4004, "the extension role needs a chrome-extension origin")
            return
        elif S["require_peer_token"] and not hmac.compare_digest(
            str(hello.get("token", "")).encode(), (self.token or "").encode()
        ):
            await ws.send(json.dumps({"type": "error", "error": "bad token (see the require_peer_token setting)"}))
            await ws.close(4001, "bad token")
            return
        if hello.get("role") == "extension":
            await self._serve_extension(ws, hello)
        elif hello.get("role") == "peer":
            await self._serve_peer(ws)
        else:
            await ws.close(4000, "unknown role")

    async def _serve_extension(self, ws, hello):
        old = self.ext
        self.ext, self.ext_info = ws, {"version": hello.get("version"), "connected_at": time.strftime("%H:%M:%S")}
        if old is not None:
            try:
                await old.close(4002, "replaced by a newer connection")
            except Exception:
                pass
        await ws.send(json.dumps({"type": "welcome", "server": __version__}))
        log("extension connected", hello.get("version"))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await ws.send('{"type":"pong"}')
                elif msg.get("type") == "result":
                    fut = self.pending.get(msg.get("id"))
                    if fut and not fut.done():
                        fut.set_result(msg)
        except ConnectionClosed:
            pass
        finally:
            if self.ext is ws:
                self.ext = None
                for fut in self.pending.values():
                    if not fut.done():
                        fut.set_exception(BridgeError("the extension disconnected during the job (tab closed / Chrome closed?)"))
                log("extension disconnected")

    async def _serve_peer(self, ws):
        try:
            req = json.loads(await ws.recv())
            try:
                res = await self.run(req["kind"], req.get("params", {}), int(req.get("timeout", 180)))
                await ws.send(json.dumps({"ok": True, "result": res}))
            except BridgeError as e:
                await ws.send(json.dumps({"ok": False, "error": str(e), "submitted": e.submitted}))
        except ConnectionClosed:
            pass

    # ---------------------------------------------------------------- jobs
    async def run(self, kind: str, params: dict, timeout: int) -> dict:
        if self.role == "peer":
            return await self._run_via_hub(kind, params, timeout)
        if kind == "status":
            return await self._status()
        self.queued += 1
        try:
            async with self.lock:  # budget checked here so queued jobs (from any session) see the latest counts
                check_budget(**{"images" if kind == "image" else "videos": 1})
                try:
                    data = await self._dispatch(kind, params, timeout)
                except BridgeError as e:
                    if e.submitted:  # e.g. moderated / timed out after submitting: quota was still used
                        record_usage(kind)
                    raise
                if data.get("submitted", True):
                    kept = len(data.get("files") or [])
                    record_usage(kind, int(data.get("generated", kept)), kept)
                return data
        finally:
            self.queued -= 1

    async def _dispatch(self, kind, params, timeout, slack=90):
        if self.ext is None:
            raise BridgeError(NO_EXTENSION)
        jid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[jid] = fut
        try:
            await self.ext.send(json.dumps({"type": "job", "id": jid, "kind": kind, "params": params, "timeout_s": timeout}))
            msg = await asyncio.wait_for(fut, timeout + slack)  # slack: opening/loading the grok tab
        except asyncio.TimeoutError:
            raise BridgeError(f"timeout: the extension did not answer within {timeout + slack}s") from None
        except ConnectionClosed:
            raise BridgeError("the extension disconnected while the job was being sent") from None
        finally:
            self.pending.pop(jid, None)
        if not msg.get("ok"):
            e = BridgeError(msg.get("error") or "the extension reported an unknown error")
            e.submitted = bool(msg.get("submitted"))
            raise e
        return msg.get("data") or {}

    async def _status(self) -> dict:
        st = {
            "bridge": "hub",
            "version": __version__,
            "port": PORT,
            "extension_connected": self.ext is not None,
            "extension": self.ext_info,
            "jobs_running_or_waiting": self.queued,
            "output_dir": str(OUTPUT),
            "settings": dict(S),
        }
        if self.ext is None:
            st["hint"] = "open Chrome with the Grok Web MCP extension loaded; its badge turns green when connected"
        elif self.lock.locked():
            st["browser"] = "busy with a job"
        else:
            try:
                st["browser"] = await self._dispatch("status", {}, 20, slack=10)
            except BridgeError as e:
                st["browser_error"] = str(e)
        return st

    async def _run_via_hub(self, kind, params, timeout):
        try:
            async with connect(f"ws://{HOST}:{PORT}", max_size=MAX_MSG, open_timeout=5) as ws:
                await ws.send(json.dumps({"role": "peer", "token": self.token or ""}))
                await ws.send(json.dumps({"kind": kind, "params": params, "timeout": timeout}))
                ans = json.loads(await asyncio.wait_for(ws.recv(), timeout + 1800))  # may wait in the hub's queue
        except (OSError, ConnectionClosed, asyncio.TimeoutError) as e:
            await self.start()  # the hub went away? take over the port
            if self.role == "hub":
                raise BridgeError(
                    "this process just became the bridge hub; the extension reconnects within ~30 s, retry then"
                ) from None
            raise BridgeError(f"cannot reach the grok-web-mcp hub on port {PORT}: {e!r}") from None
        if not ans.get("ok"):
            e = BridgeError(ans.get("error") or "hub error")
            e.submitted = bool(ans.get("submitted"))
            raise e
        res = ans["result"]
        if kind == "status":
            res["bridge"] = "peer (jobs go through another grok-web-mcp process that owns the port)"
        return res


# -------------------------------------------------------------------- files
def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else OUTPUT / p


def _out_dir(out_dir: str) -> Path:
    p = _resolve(out_dir or ".")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _name(name: str, prompt: str) -> str:
    n = re.sub(r"[^\w\-.]+", "_", name or "").strip("._")
    if not n:
        n = (re.sub(r"[^A-Za-z0-9]+", "_", prompt).strip("_").lower()[:32] or "grok") + time.strftime("_%H%M%S")
    return n


def _parse_size(size: str):
    if not size:
        return None
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*", str(size))
    if not m or not all(64 <= int(v) <= 8192 for v in m.groups()):
        raise BridgeError('size must look like "1344x768" (each side 64..8192)')
    return int(m[1]), int(m[2])


def _fit(p: Path, max_side: int, size) -> list[int]:
    """size=(w,h): scale to cover + center crop to exactly w x h. Else max_side: shrink so the long side <= max_side."""
    from PIL import Image, ImageOps

    with Image.open(p) as im:
        im.load()
    if size:
        im = ImageOps.fit(im, size, Image.LANCZOS, centering=(0.5, 0.5))
    elif max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    else:
        return list(im.size)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        im.convert("RGB").save(p, "JPEG", quality=95)
    else:
        im.save(p, quality=95) if p.suffix.lower() == ".webp" else im.save(p)
    return list(im.size)


def _save(
    files: list, out_dir: str, name: str, start: int = 1, max_side: int = 0, size=None, keep_original: bool = False
) -> tuple[list[str], list]:
    d, paths, sizes = _out_dir(out_dir), [], []
    for k, f in enumerate(files, start):
        ext = EXT.get(str(f.get("mime", "")).lower().split(";")[0], "bin")
        p = d / f"{name}_{k}.{ext}"
        i = 2
        while p.exists():  # never overwrite: name_1_2.jpg, name_1_3.jpg ...
            p = d / f"{name}_{k}_{i}.{ext}"
            i += 1
        p.write_bytes(base64.b64decode(f["b64"]))
        if (max_side or size) and ext in ("jpg", "png", "webp"):
            if keep_original:
                shutil.copyfile(p, p.with_name(f"{p.stem}_orig{p.suffix}"))
            sizes.append(_fit(p, max_side, size))
        paths.append(str(p))
    return paths, sizes


# -------------------------------------------------------------------- MCP
hub: Hub | None = None
_sessions = 0  # HTTP transports open one MCP session per client; they all share one hub


@asynccontextmanager
async def lifespan(_app):
    global hub, _sessions
    if hub is None:
        hub = Hub(load_token())
        load_limits()  # creates the limits file with defaults on first run
        await hub.start()
    _sessions += 1
    try:
        yield
    finally:
        _sessions -= 1
        if _sessions == 0:
            await hub.stop()
            hub = None


mcp = FastMCP("grok-web-mcp", lifespan=lifespan)


def _hints(read_only: bool, destructive: bool, idempotent: bool, open_world: bool) -> ToolAnnotations:
    """MCP tool annotations: all four hints set explicitly, so hosts can tell users what a tool does before it runs.
    open_world = the tool reaches grok.com through the browser; generation is never idempotent (each call is a new
    Grok submission) and never destructive (files are never overwritten)."""
    return ToolAnnotations(
        readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=idempotent, openWorldHint=open_world
    )


@mcp.tool(
    title="Bridge status",
    annotations=_hints(read_only=True, destructive=False, idempotent=True, open_world=True),
)
async def grok_status() -> dict:
    """Bridge health: is the Chrome extension connected, is a grok.com/imagine tab open, is the user logged in."""
    return await hub.run("status", {}, 20)


@mcp.tool(
    title="Settings in effect",
    annotations=_hints(read_only=True, destructive=False, idempotent=True, open_world=False),
)
async def grok_settings() -> dict:
    """The settings in effect (defaults < config.json < GROK_WEB_MCP_* env vars), where each came from, and what each
    one does. Settings are read when the server starts; restart the MCP client after changing them."""
    return {
        "settings": S,
        "source": SOURCES,
        "help": {k: v[1] for k, v in settings.OPTIONS.items()},
        "files": {"config": str(CONFIG_FILE), "limits": str(LIMITS_FILE), "extensions": str(EXTENSIONS_FILE)},
    }


@mcp.tool(
    title="Today's usage and limits",
    annotations=_hints(read_only=True, destructive=False, idempotent=True, open_world=False),
)
async def grok_usage() -> dict:
    """Today's Grok Imagine usage (image submissions, images generated/kept, videos) against the daily limits."""
    lim, u = load_limits(), usage_today()
    return {
        "date": _today(),
        "used": u,
        "limits": lim,
        "quota_guard": S["quota_guard"],
        "remaining": {
            "image_submissions": max(0, lim["max_image_submissions_per_day"] - u["image_submissions"]),
            "videos": max(0, lim["max_videos_per_day"] - u["videos"]),
        },
        "note": f"each image submission returns {PER_SUBMIT} images; n>{PER_SUBMIT} costs ceil(n/{PER_SUBMIT}) submissions",
        "files": {"usage": str(USAGE_FILE), "limits": str(LIMITS_FILE)},
    }


@mcp.tool(
    title="Change daily limits",
    annotations=_hints(read_only=False, destructive=False, idempotent=True, open_world=False),
)
async def grok_set_limits(
    max_image_submissions_per_day: int | None = None,
    max_videos_per_day: int | None = None,
    max_jobs_per_batch: int | None = None,
    confirm_over: int | None = None,
) -> dict:
    """Change the daily Grok quota guard (only when the user asked for it). Omitted values stay as they are."""
    lim = load_limits()
    for k, v in (
        ("max_image_submissions_per_day", max_image_submissions_per_day),
        ("max_videos_per_day", max_videos_per_day),
        ("max_jobs_per_batch", max_jobs_per_batch),
        ("confirm_over", confirm_over),
    ):
        if v is not None:
            if int(v) < 0:
                raise BridgeError(f"{k} must be >= 0")
            lim[k] = int(v)
    _write_json(LIMITS_FILE, lim)
    return await grok_usage()


def _job(prompt, aspect, n, mode, max_side, size) -> dict:
    """Validate one image job; returns the normalized params + how many submissions it costs."""
    aspect, n, mode = aspect or S["default_aspect"], n or S["default_n"], mode or S["default_mode"]
    if aspect not in ASPECTS:
        raise BridgeError(f"aspect must be one of {', '.join(ASPECTS)}")
    if not str(prompt).strip():
        raise BridgeError("empty prompt")
    n = int(n)
    if not 1 <= n <= 4:
        raise BridgeError("n must be 1..4 (images to keep; every 2 images = one Grok submission)")
    mode = (mode or "").lower()
    if mode not in MODES:
        raise BridgeError('mode must be "speed", "quality" or "" (leave as is)')
    max_side = int(max_side or 0)
    if max_side and not 64 <= max_side <= 8192:
        raise BridgeError("max_side must be 64..8192 (or 0 for no resize)")
    return {
        "prompt": prompt,
        "aspect": aspect,
        "n": n,
        "mode": mode,
        "max_side": max_side,
        "size": _parse_size(size),
        "subs": math.ceil(n / PER_SUBMIT),
    }


async def _image(j: dict, out_dir, name, timeout_s, keep_original) -> dict:
    name = _name(name, j["prompt"])
    files, sizes, generated, warnings, aspect_ok, secs, subs = [], [], 0, [], True, 0, 0
    for i in range(j["subs"]):
        k = min(PER_SUBMIT, j["n"] - PER_SUBMIT * i)
        try:
            data = await hub.run(
                "image", {"prompt": j["prompt"], "aspect": j["aspect"], "n": k, "mode": j["mode"]}, int(timeout_s)
            )
        except BridgeError as e:
            subs += e.submitted
            if not files:
                raise
            warnings.append(f"submission {i + 1}/{j['subs']} failed: {e}")
            break
        subs += 1
        if not data.get("files"):
            warnings.append(f"submission {i + 1} returned no images")
            continue
        p, s = _save(data["files"], out_dir, name, len(files) + 1, j["max_side"], j["size"], keep_original)
        files += p
        sizes += s
        generated += int(data.get("generated", len(data["files"])))
        aspect_ok = aspect_ok and data.get("aspectOk") is not False
        secs += int(data.get("secs") or 0)
        warnings += [w for w in (data.get("warning"), data.get("modeWarning")) if w]
    if not files:
        raise BridgeError("grok returned no images")
    out = {"files": files, "kept": len(files), "generated": generated, "submissions": subs, "aspect_ok": aspect_ok, "secs": secs}
    if sizes:
        out["sizes"] = sizes
    if warnings:
        out["warning"] = "; ".join(warnings)
    return out


@mcp.tool(
    title="Generate images with Grok Imagine",
    annotations=_hints(read_only=False, destructive=False, idempotent=False, open_world=True),
)
async def grok_generate_image(
    prompt: str,
    aspect: str = "",
    out_dir: str = "",
    name: str = "",
    n: int = 0,
    mode: str = "",
    max_side: int = 0,
    size: str = "",
    keep_original: bool = False,
    timeout_s: int = 0,
) -> dict:
    """Generate images with Grok Imagine in the user's logged-in Chrome (no API key; uses the user's own Grok
    plan and daily quota: see grok_usage(); jobs over the limits are refused).

    Omitted aspect / n / mode / timeout_s use the configured defaults (see grok_settings).
    aspect: 16:9 | 9:16 | 1:1 | 2:3 | 3:2. n: images to keep, 1..4 (one submission gives 2; n=3/4 = 2 submissions).
    mode: "speed" | "quality" (a warning, not a failure, if the page control is not found).
    max_side: shrink saved files so the long side <= max_side (e.g. 1344). size: exact "WxH" (e.g. "1344x768"),
    scale to cover + center crop (overrides max_side). keep_original: also keep <file>_orig.<ext>.
    out_dir: absolute, or relative to the output root (GROK_WEB_MCP_OUTPUT_DIR, default ~/grok-web-mcp).
    Files are <name>_1.jpg, <name>_2.jpg ... (never overwritten).
    Returns {"files", "kept", "generated", "submissions", "aspect_ok", "secs", "sizes"?, "warning"?}.
    """
    j = _job(prompt, aspect, n, mode, max_side, size)
    check_budget(images=j["subs"])  # the whole job must fit before anything is submitted
    return await _image(j, out_dir, name, timeout_s or S["image_timeout_s"], keep_original)


@mcp.tool(
    title="Generate a batch of images",
    annotations=_hints(read_only=False, destructive=False, idempotent=False, open_world=True),
)
async def grok_batch(jobs: list[dict], out_dir: str = "", confirm: bool = False) -> dict:
    """Run several image jobs one after another. Each job: {"prompt", "name", "aspect"?, "n"? (1..4, default 1),
    "mode"?, "max_side"?, "size"?, "keep_original"?, "timeout_s"?, "out_dir"?}.
    The whole batch is checked first (job count, total submissions vs today's budget); nothing runs if it does not fit.
    Batches needing more than `confirm_over` submissions need confirm=true (ask the user first).
    Returns {"results": [...], "submissions_planned", "usage"}; a failed job gets an "error" and the batch goes on,
    except budget/no-extension errors, which stop it and mark the rest "skipped"."""
    lim = load_limits()
    if not jobs:
        raise BridgeError("empty batch")
    if S["quota_guard"] and len(jobs) > lim["max_jobs_per_batch"]:
        raise BridgeError(
            f"Grok budget: {len(jobs)} jobs > max_jobs_per_batch={lim['max_jobs_per_batch']}; split the "
            f"batch or raise the limit with grok_set_limits(max_jobs_per_batch=N) after asking the user."
        )
    norm = []
    for i, j in enumerate(jobs, 1):
        try:
            norm.append(
                _job(
                    j.get("prompt", ""),
                    j.get("aspect", ""),
                    j.get("n", 0),
                    j.get("mode", ""),
                    j.get("max_side", 0),
                    j.get("size", ""),
                )
            )
        except BridgeError as e:
            raise BridgeError(f"job {i} ({j.get('name')}): {e}; nothing was submitted") from None
    total = sum(j["subs"] for j in norm)
    if S["quota_guard"] and total > lim["confirm_over"] and not confirm:
        raise BridgeError(
            f"this batch needs {total} Grok submissions (> confirm_over={lim['confirm_over']}); "
            f"ask the user, then call again with confirm=true. Nothing was submitted."
        )
    check_budget(images=total)
    results, stop = [], ""
    for j, nj in zip(jobs, norm, strict=True):
        if stop:
            results.append({"name": j.get("name"), "skipped": stop})
            continue
        try:
            r = await _image(
                nj,
                j.get("out_dir", out_dir),
                j.get("name", ""),
                j.get("timeout_s") or S["image_timeout_s"],
                bool(j.get("keep_original")),
            )
            results.append({"name": j.get("name"), **r})
        except BridgeError as e:
            results.append({"name": j.get("name"), "error": str(e)})
            if str(e).startswith(("no extension connected", "Grok budget")):
                stop = "not run: " + str(e).split(":")[0]
    return {"results": results, "submissions_planned": total, "usage": usage_today()}


@mcp.tool(
    title="Generate a video with Grok Imagine (experimental)",
    annotations=_hints(read_only=False, destructive=False, idempotent=False, open_world=True),
)
async def grok_generate_video(
    prompt: str, image_path: str = "", aspect: str = "", out_dir: str = "", name: str = "", timeout_s: int = 0
) -> dict:
    """EXPERIMENTAL: make a Grok Imagine video (about 6 s) from a prompt, optionally animating a local image
    (image_path: jpg/png/webp). Videos cost much more quota than images and are capped by max_videos_per_day
    (see grok_usage). Relies on Grok's current web UI, which changes often. Returns {"files": [mp4 path]}."""
    aspect = aspect or S["default_aspect"]
    if aspect not in ASPECTS:
        raise BridgeError(f"aspect must be one of {', '.join(ASPECTS)}")
    params = {"prompt": prompt, "aspect": aspect}
    if image_path:
        p = _resolve(image_path)
        if not p.is_file():
            raise BridgeError(f"image not found: {p}")
        mime = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
        params.update(image_b64=base64.b64encode(p.read_bytes()).decode(), image_mime=mime, image_name=p.name)
    elif not prompt.strip():
        raise BridgeError("give a prompt, an image_path, or both")
    check_budget(videos=1)
    data = await hub.run("video", params, int(timeout_s or S["video_timeout_s"]))
    if not data.get("files"):
        raise BridgeError("grok returned no video")
    return {"files": _save(data["files"], out_dir, _name(name, prompt))[0], "secs": data.get("secs"), "experimental": True}


USAGE = """grok-web-mcp: MCP server for Grok Imagine through your own grok.com tab

  grok-web-mcp                 stdio server (what Claude, Cursor, VS Code, Codex, Gemini CLI... launch)
  grok-web-mcp --http [PORT]   Streamable HTTP server on http://127.0.0.1:PORT/mcp (default 8000)
  grok-web-mcp --sse [PORT]    legacy SSE server on http://127.0.0.1:PORT/sse (default 8000)
  grok-web-mcp --init          write ~/.grok-web-mcp/config.json with every setting at its default
  grok-web-mcp --config        show the settings in effect and where each one came from
  grok-web-mcp --version       print the version"""


def main():
    args = sys.argv[1:]
    arg = args[0] if args else ""
    if arg in ("--version", "-V"):
        print(f"grok-web-mcp {__version__}")
    elif arg == "--init":  # write config.json and limits.json (and token.txt when required) without starting the server
        made = settings.write_template()
        load_limits()
        load_token()
        print(f"grok-web-mcp {__version__}: {'created' if made else 'kept the existing'} {CONFIG_FILE}")
        print(f"images are saved under {OUTPUT}")
    elif arg == "--config":  # the settings in effect and where each one came from
        width = max(map(len, S))
        for k, v in S.items():
            print(f"{k:<{width}}  {json.dumps(v):<18} ({SOURCES[k]})")
        print(f"\nconfig file: {CONFIG_FILE}")
    elif arg in ("--http", "--sse"):
        port = int(args[1]) if len(args) > 1 else 8000
        if port == PORT:
            sys.exit(f"[grok-web-mcp] port {port} is the extension bridge port; pick another for {arg}")
        mcp.settings.host, mcp.settings.port = HOST, port  # loopback only, like the bridge
        transport = "streamable-http" if arg == "--http" else "sse"
        log(f"{transport} MCP endpoint on http://{HOST}:{port}{'/mcp' if arg == '--http' else '/sse'}")
        mcp.run(transport=transport)
    elif arg in ("-h", "--help"):
        print(USAGE)
    elif arg:
        sys.exit(f"unknown option {arg}\n\n{USAGE}")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()

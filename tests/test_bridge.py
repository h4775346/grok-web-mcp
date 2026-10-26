"""End-to-end test without Chrome or grok.com: starts the server over MCP stdio, connects a fake extension that
answers jobs with small files, and checks that the tools save them, enforce the quota guard and the socket auth.

Uses a throw-away home, output dir and port, so a real config and a running server are never touched.
"""

import asyncio
import base64
import io
import json
import os
import socket
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

EXT_ORIGIN = "chrome-extension://fakeextensionid"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def jpeg(w=1792, h=1008) -> bytes:  # what Grok returns for 16:9
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 40)).save(b, "JPEG", quality=80)
    return b.getvalue()


JPG, MP4 = jpeg(), b"\x00\x00\x00\x18ftypmp42fake-video-bytes"


async def fake_extension(url: str, seen: dict, ready: asyncio.Event, origin: str = EXT_ORIGIN):
    async with connect(url, max_size=None, additional_headers={"Origin": origin}) as ws:
        await ws.send(json.dumps({"type": "hello", "role": "extension", "version": "fake"}))
        assert json.loads(await ws.recv())["type"] == "welcome"
        ready.set()
        async for raw in ws:
            m = json.loads(raw)
            if m.get("type") != "job":
                continue
            p, kind = m["params"], m["kind"]
            seen.setdefault(kind, []).append(p)
            if kind == "status":
                res = {
                    "ok": True,
                    "data": {"tab": {"id": 1, "url": "https://grok.com/imagine"}, "page": {"editor": True, "loggedIn": True}},
                }
            elif kind == "image" and "FAIL" in p["prompt"]:  # moderated after the submit: quota was used
                res = {"ok": False, "error": "timeout: no images appeared (fake)", "submitted": True}
            elif kind == "image":
                f = {"mime": "image/jpeg", "b64": base64.b64encode(JPG).decode()}
                data = {"files": [f] * p["n"], "submitted": True, "generated": 2, "kept": p["n"], "aspectOk": True, "secs": 1}
                if p.get("mode") == "quality":
                    data["modeWarning"] = "speed/quality control not found (fake)"
                res = {"ok": True, "data": data}
            else:
                res = {
                    "ok": True,
                    "data": {
                        "files": [{"mime": "video/mp4", "b64": base64.b64encode(MP4).decode()}],
                        "submitted": True,
                        "secs": 2,
                    },
                }
            await ws.send(json.dumps({"type": "result", "id": m["id"], **res}))


async def call(s: ClientSession, tool, **args):
    r = await s.call_tool(tool, args)
    text = r.content[0].text if r.content else ""
    return r.isError, (text if r.isError else json.loads(text) if text.startswith(("{", "[")) else text)


async def used(s) -> dict:
    return (await call(s, "grok_usage"))[1]["used"]


async def close_code(url: str, hello: dict, origin: str | None = None) -> int | None:
    try:
        async with connect(url, additional_headers={"Origin": origin} if origin else None) as ws:
            await ws.send(json.dumps(hello))
            while True:
                await ws.recv()
    except ConnectionClosed as e:
        return e.rcvd.code if e.rcvd else None


async def scenario(tmp: Path):
    port = free_port()
    url = f"ws://127.0.0.1:{port}"
    home, out = tmp / "home", tmp / "out"
    env = {**os.environ, "GROK_WEB_MCP_PORT": str(port), "GROK_WEB_MCP_HOME": str(home), "GROK_WEB_MCP_OUTPUT_DIR": str(out)}
    env.pop("GROK_WEB_MCP_EXTENSION_IDS", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "grok_web_mcp"], env=env)
    seen: dict = {}
    async with stdio_client(params) as (r, w), ClientSession(r, w) as hub:
        await hub.initialize()
        tools = {t.name for t in (await hub.list_tools()).tools}
        assert {
            "grok_status",
            "grok_generate_image",
            "grok_batch",
            "grok_generate_video",
            "grok_usage",
            "grok_set_limits",
        } <= tools
        token = (home / "token.txt").read_text().strip()
        assert len(token) >= 32, "token file created on first run"
        assert json.loads((home / "limits.json").read_text()) == {
            "max_image_submissions_per_day": 30,
            "max_videos_per_day": 5,
            "max_jobs_per_batch": 12,
            "confirm_over": 10,
        }

        err, st = await call(hub, "grok_status")
        assert not err and st["extension_connected"] is False and st["bridge"] == "hub"
        err, msg = await call(hub, "grok_generate_image", prompt="x")
        assert err and "no extension connected" in msg
        assert (await used(hub))["image_submissions"] == 0, "nothing counted when no extension"

        # ---- socket auth
        assert await close_code(url, {"role": "peer", "token": "wrong"}) == 4001, "wrong token rejected"
        assert await close_code(url, {"role": "extension"}) == 4004, "extension role needs a chrome-extension origin"
        assert await close_code(url, {"role": "extension", "token": token}, "https://evil.example") == 4003, "web page refused"

        ready = asyncio.Event()
        ext = asyncio.create_task(fake_extension(url, seen, ready))
        await asyncio.wait_for(ready.wait(), 5)
        assert json.loads((home / "extensions.json").read_text())["allowed"] == ["fakeextensionid"], "first extension pinned"
        assert await close_code(url, {"role": "extension"}, "chrome-extension://otherextension") == 4004, (
            "other extension refused"
        )

        err, st = await call(hub, "grok_status")
        assert not err and st["extension_connected"] and st["browser"]["page"]["loggedIn"]

        # ---- validation happens before anything reaches Grok
        err, msg = await call(hub, "grok_generate_image", prompt="x", aspect="4:3")
        assert err and "aspect must be one of" in msg
        err, msg = await call(hub, "grok_generate_image", prompt="x", n=5)
        assert err and "n must be 1..4" in msg
        err, msg = await call(hub, "grok_generate_image", prompt="x", size="big")
        assert err and "size must look like" in msg and "image" not in seen

        # ---- images
        err, res = await call(
            hub, "grok_generate_image", prompt="a cat on the Nile", aspect="9:16", out_dir="cats", name="cat", n=2
        )
        files = [Path(f) for f in res["files"]]
        assert not err and [f.name for f in files] == ["cat_1.jpg", "cat_2.jpg"]
        assert all(f.parent == out / "cats" and f.read_bytes() == JPG for f in files), "relative out_dir under the output root"
        assert res["submissions"] == 1 and res["generated"] == 2
        assert seen["image"][-1] == {"prompt": "a cat on the Nile", "aspect": "9:16", "n": 2, "mode": ""}

        err, res = await call(hub, "grok_generate_image", prompt="again", out_dir="cats", name="cat")
        assert len(res["files"]) == 1 and Path(res["files"][0]).name == "cat_1_2.jpg", "never overwrites"
        assert await used(hub) == {"image_submissions": 2, "images_generated": 4, "images_kept": 3, "videos": 0}

        k = len(seen["image"])
        err, res = await call(hub, "grok_generate_image", prompt="three", out_dir=str(tmp / "n3"), name="q", n=3)
        assert not err and len(res["files"]) == 3 and res["submissions"] == 2
        assert [p["n"] for p in seen["image"][k:]] == [2, 1]
        assert [Path(f).name for f in res["files"]] == ["q_1.jpg", "q_2.jpg", "q_3.jpg"]
        assert (await used(hub))["image_submissions"] == 4

        err, res = await call(
            hub, "grok_generate_image", prompt="crop", out_dir="sz", name="c", size="1344x768", keep_original=True
        )
        f = Path(res["files"][0])
        assert not err and Image.open(f).size == (1344, 768) and res["sizes"] == [[1344, 768]]
        assert Image.open(f.with_name("c_1_orig.jpg")).size == (1792, 1008)
        err, res = await call(hub, "grok_generate_image", prompt="shrink", out_dir="sz", name="m", max_side=1000)
        f = Path(res["files"][0])
        assert not err and max(Image.open(f).size) == 1000 and Image.open(f).size[1] in (562, 563)
        assert not f.with_name("m_1_orig.jpg").exists()

        err, res = await call(hub, "grok_generate_image", prompt="hq", name="hq", mode="quality")
        assert not err and seen["image"][-1]["mode"] == "quality" and "control not found" in res.get("warning", "")
        err, msg = await call(hub, "grok_generate_image", prompt="x", mode="ultra")
        assert err and "mode must be" in msg

        err, res = await call(
            hub,
            "grok_batch",
            out_dir="batch",
            jobs=[
                {"prompt": "one", "name": "b1"},
                {"prompt": "FAIL two", "name": "b2"},
                {"prompt": "three", "name": "b3", "aspect": "1:1"},
            ],
        )
        rs = res["results"]
        assert not err and len(rs) == 3 and "error" in rs[1] and len(rs[0]["files"]) == 1 and len(rs[2]["files"]) == 1
        assert (await used(hub))["image_submissions"] == 10, "failed-after-submit job still counted"

        # ---- limits
        err, res = await call(hub, "grok_set_limits", max_image_submissions_per_day=12)
        assert not err and res["limits"]["max_image_submissions_per_day"] == 12 and res["remaining"]["image_submissions"] == 2

        k = len(seen["image"])
        err, msg = await call(hub, "grok_batch", jobs=[{"prompt": "a", "name": "x1", "n": 4}, {"prompt": "b", "name": "x2"}])
        assert err and "Grok budget" in msg and "10/12" in msg and len(seen["image"]) == k
        err, msg = await call(hub, "grok_batch", jobs=[{"prompt": "p", "name": f"j{i}"} for i in range(13)])
        assert err and "max_jobs_per_batch=12" in msg and len(seen["image"]) == k
        err, msg = await call(hub, "grok_batch", jobs=[{"prompt": "p", "name": "ok"}, {"prompt": "", "name": "bad"}])
        assert err and "job 2" in msg and len(seen["image"]) == k

        await call(hub, "grok_set_limits", confirm_over=1)
        jobs = [{"prompt": "c1", "name": "c1"}, {"prompt": "c2", "name": "c2"}]
        err, msg = await call(hub, "grok_batch", out_dir="batch", jobs=jobs)
        assert err and "confirm=true" in msg and len(seen["image"]) == k
        err, res = await call(hub, "grok_batch", out_dir="batch", jobs=jobs, confirm=True)
        assert not err and all("files" in r for r in res["results"]) and res["usage"]["image_submissions"] == 12

        err, msg = await call(hub, "grok_generate_image", prompt="one more")
        assert err and "Grok budget" in msg and "12/12" in msg and len(seen["image"]) == k + 2

        # ---- video (experimental) is counted and limited too
        await call(hub, "grok_set_limits", max_videos_per_day=1)
        err, res = await call(
            hub,
            "grok_generate_video",
            prompt="slow push in",
            image_path="cats/cat_1.jpg",
            out_dir="vid",
            name="clip",
            aspect="9:16",
        )
        v = seen["video"][-1]
        assert not err and Path(res["files"][0]).read_bytes() == MP4
        assert base64.b64decode(v["image_b64"]) == JPG and v["image_mime"] == "image/jpeg" and v["aspect"] == "9:16"
        err, msg = await call(hub, "grok_generate_video", prompt="again")
        assert err and "Grok budget" in msg and "1/1" in msg and len(seen["video"]) == 1
        err, u = await call(hub, "grok_usage")
        assert u["used"] == {"image_submissions": 12, "images_generated": 22, "images_kept": 13, "videos": 1}
        assert u["remaining"] == {"image_submissions": 0, "videos": 0}

        # ---- a second MCP process (another client session) becomes a peer and goes through the hub
        async with stdio_client(params) as (r2, w2), ClientSession(r2, w2) as peer:
            await peer.initialize()
            err, st = await call(peer, "grok_status")
            assert not err and st["bridge"].startswith("peer") and st["extension_connected"]
            err, msg = await call(peer, "grok_generate_image", prompt="via peer", name="p")
            assert err and "Grok budget" in msg, "peer sees the same budget"
            await call(peer, "grok_set_limits", max_image_submissions_per_day=13)
            err, res = await call(peer, "grok_generate_image", prompt="via peer", name="p")
            assert not err and Path(res["files"][0]).exists()
            assert (await used(hub))["image_submissions"] == 13, "peer job counted once by the hub"

        ext.cancel()


def test_bridge_end_to_end(tmp_path):
    asyncio.run(asyncio.wait_for(scenario(tmp_path), 120))

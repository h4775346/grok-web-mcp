"""Settings: config.json + GROK_WEB_MCP_* env vars change defaults, and every optional protection can be turned off."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_bridge import call, close_code, fake_extension, free_port
from websockets.asyncio.client import connect


def env_for(home: Path, out: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GROK_WEB_MCP_")}
    return {**env, "GROK_WEB_MCP_HOME": str(home), "GROK_WEB_MCP_OUTPUT_DIR": str(out), **extra}


async def raw_peer(url: str, req: dict, token: str | None = None) -> dict:
    """A local client speaking the peer protocol directly (what a second MCP session does)."""
    async with connect(url) as ws:
        await ws.send(json.dumps({"role": "peer", **({"token": token} if token is not None else {})}))
        await ws.send(json.dumps(req))
        return json.loads(await ws.recv())


async def options_off(tmp: Path):
    port = free_port()
    url = f"ws://127.0.0.1:{port}"
    home, out = tmp / "home", tmp / "out"
    home.mkdir()
    # config.json sets most options; one is overridden by an env var to check the precedence
    (home / "config.json").write_text(
        json.dumps(
            {
                "port": port,
                "require_peer_token": False,
                "pin_extension": False,
                "quota_guard": False,
                "default_aspect": "1:1",
                "default_n": 2,
                "default_mode": "quality",
            }
        )
    )
    env = env_for(home, out, GROK_WEB_MCP_DEFAULT_ASPECT="9:16")
    params = StdioServerParameters(command=sys.executable, args=["-m", "grok_web_mcp"], env=env)
    seen: dict = {}
    async with stdio_client(params) as (r, w), ClientSession(r, w) as hub:
        await hub.initialize()
        err, st = await call(hub, "grok_settings")
        assert not err and st["settings"]["default_aspect"] == "9:16" and st["source"]["default_aspect"] == "env"
        assert st["source"]["require_peer_token"] == "config.json" and st["source"]["image_timeout_s"] == "default"
        assert "require_peer_token" in st["help"]
        assert not (home / "token.txt").exists(), "no token file when require_peer_token is off"

        # web pages are still refused even with every option off
        assert await close_code(url, {"role": "peer"}, "https://evil.example") == 4003

        # pin_extension off: any extension id may connect, nothing is pinned
        ready = asyncio.Event()
        ext = asyncio.create_task(fake_extension(url, seen, ready, origin="chrome-extension://anyid"))
        await asyncio.wait_for(ready.wait(), 5)
        assert not (home / "extensions.json").exists()

        # configured defaults reach the extension
        err, res = await call(hub, "grok_generate_image", prompt="defaults", name="d")
        assert not err and len(res["files"]) == 2
        assert seen["image"][-1] == {"prompt": "defaults", "aspect": "9:16", "n": 2, "mode": "quality"}

        # require_peer_token off: a local peer without a token is served
        ans = await raw_peer(url, {"kind": "image", "params": {"prompt": "peer", "aspect": "1:1", "n": 1, "mode": ""}})
        assert ans["ok"] and ans["result"]["files"], ans

        # quota_guard off: limits are not enforced, usage is still recorded
        await call(hub, "grok_set_limits", max_image_submissions_per_day=1, confirm_over=0, max_jobs_per_batch=1)
        err, res = await call(hub, "grok_batch", jobs=[{"prompt": "a", "name": "a"}, {"prompt": "b", "name": "b"}])
        assert not err and all("files" in x for x in res["results"])
        err, u = await call(hub, "grok_usage")
        assert u["quota_guard"] is False and u["used"]["image_submissions"] == 4
        ext.cancel()


async def token_on(tmp: Path):
    """Default (require_peer_token on): a peer without the token is refused, with it it is served."""
    port = free_port()
    url = f"ws://127.0.0.1:{port}"
    home, out = tmp / "home2", tmp / "out2"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "grok_web_mcp"], env=env_for(home, out, GROK_WEB_MCP_PORT=str(port))
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as hub:
        await hub.initialize()
        token = (home / "token.txt").read_text().strip()
        assert await close_code(url, {"role": "peer"}) == 4001
        ans = await raw_peer(url, {"kind": "status", "params": {}}, token=token)
        assert ans["ok"] and ans["result"]["bridge"] == "hub"


def test_options_can_be_turned_off(tmp_path):
    asyncio.run(asyncio.wait_for(options_off(tmp_path), 60))


def test_peer_token_required_by_default(tmp_path):
    asyncio.run(asyncio.wait_for(token_on(tmp_path), 60))


def run_cli(tmp: Path, *args, **extra):
    env = env_for(tmp / "h", tmp / "o", **extra)
    return subprocess.run([sys.executable, "-m", "grok_web_mcp", *args], env=env, capture_output=True, text=True, timeout=30)


def test_cli_init_and_config(tmp_path):
    r = run_cli(tmp_path, "--init")
    assert r.returncode == 0 and (tmp_path / "h" / "config.json").exists() and (tmp_path / "h" / "token.txt").exists()
    cfg = json.loads((tmp_path / "h" / "config.json").read_text())
    assert cfg["require_peer_token"] is True and cfg["default_aspect"] == "16:9"
    r = run_cli(tmp_path, "--config", GROK_WEB_MCP_QUOTA_GUARD="off")
    assert r.returncode == 0 and "quota_guard" in r.stdout and "(env)" in r.stdout


def test_invalid_setting_is_reported(tmp_path):
    r = run_cli(tmp_path, "--config", GROK_WEB_MCP_DEFAULT_ASPECT="4:3")
    assert r.returncode != 0 and "default_aspect" in r.stderr

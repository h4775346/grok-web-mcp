"""HTTP transports: --http (Streamable HTTP) and --sse serve the same tools, and every HTTP session shares one hub."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from test_bridge import call, fake_extension, free_port


def start(tmp, flag: str):
    bridge, http = free_port(), free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GROK_WEB_MCP_")}
    env.update(GROK_WEB_MCP_HOME=str(tmp / "home"), GROK_WEB_MCP_OUTPUT_DIR=str(tmp / "out"), GROK_WEB_MCP_PORT=str(bridge))
    proc = subprocess.Popen(
        [sys.executable, "-m", "grok_web_mcp", flag, str(http)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    path = "/mcp" if flag == "--http" else "/sse"
    for _ in range(150):  # wait until the HTTP port accepts connections
        try:
            socket.create_connection(("127.0.0.1", http), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    return proc, f"http://127.0.0.1:{http}{path}", f"ws://127.0.0.1:{bridge}"


async def http_sessions(url: str, ws_url: str):
    seen: dict = {}
    async with streamablehttp_client(url) as (r1, w1, _), ClientSession(r1, w1) as a:
        await a.initialize()
        assert "grok_generate_image" in {t.name for t in (await a.list_tools()).tools}
        ready = asyncio.Event()
        ext = asyncio.create_task(fake_extension(ws_url, seen, ready))
        await asyncio.wait_for(ready.wait(), 5)
        async with streamablehttp_client(url) as (r2, w2, _), ClientSession(r2, w2) as b:
            await b.initialize()
            # both sessions see the extension through the same hub and share one usage ledger
            err, st = await call(b, "grok_status")
            assert not err and st["bridge"] == "hub" and st["extension_connected"]
            err, res = await call(a, "grok_generate_image", prompt="from a", name="a")
            assert not err and res["files"]
            err, res = await call(b, "grok_generate_image", prompt="from b", name="b")
            assert not err and res["files"]
            err, u = await call(a, "grok_usage")
            assert u["used"]["image_submissions"] == 2
        ext.cancel()


async def sse_session(url: str):
    async with sse_client(url) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        err, st = await call(s, "grok_settings")
        assert not err and json.dumps(st["settings"])


def stop(proc):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_streamable_http(tmp_path):
    proc, url, ws_url = start(tmp_path, "--http")
    try:
        asyncio.run(asyncio.wait_for(http_sessions(url, ws_url), 60))
    finally:
        stop(proc)


def test_sse(tmp_path):
    proc, url, _ = start(tmp_path, "--sse")
    try:
        asyncio.run(asyncio.wait_for(sse_session(url), 60))
    finally:
        stop(proc)

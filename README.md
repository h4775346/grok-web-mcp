<div align="center">

<img src="extension/icons/128.png" width="96" alt="Grok Web MCP logo">

# Grok Web MCP

**Generate Grok Imagine images and videos from Claude, Cursor, VS Code or any MCP client,
using the grok.com account you are already logged into. No xAI API key. No per-image billing.**

[![CI](https://github.com/h4775346/grok-web-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/h4775346/grok-web-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![MCP](https://img.shields.io/badge/MCP-server-ff7a1a)
![Chrome MV3](https://img.shields.io/badge/Chrome-MV3%20extension-4285F4)

<img src="docs/architecture.svg" alt="MCP client → grok-web-mcp → Chrome extension → grok.com/imagine" width="100%">

</div>

---

## Why

Every other Grok Imagine MCP server calls the paid xAI API. If you already pay for a Grok plan, you are paying twice.

**Grok Web MCP** drives the Grok Imagine page in *your own* Chrome, through a small companion extension, and hands
the finished files back to your AI assistant. Ask Claude for "a 9:16 cover for my podcast, two options", and two
JPGs land in a folder a few seconds later.

| | Grok Web MCP | API-based Grok MCPs |
|---|---|---|
| xAI API key | **not needed** | required |
| Cost | **your existing Grok plan** | billed per image / per video |
| Setup | one command + load the extension | API key + billing |
| Quota protection | **built-in daily limits and batch pre-check** | usually none |
| Several AI sessions at once | **shared queue** | separate |

## Features

- 🖼️ **Images**: aspect ratio (16:9, 9:16, 1:1, 2:3, 3:2), Speed / Quality mode, 1 to 4 images per call.
- 🎬 **Video** (experimental): text-to-video or animate a local image.
- 📦 **Batches**: run up to a dozen prompts in one call; the whole batch is priced and validated before anything is submitted.
- 📐 **Exact output sizes**: `size="1344x768"` cover-crops to exact pixels (ready for video models), or `max_side` to shrink.
- 🛡️ **Quota guard**: a per-day ledger with limits you control, so an over-eager agent cannot burn your daily Grok allowance.
- 🔁 **Multi-session**: open five Claude Code windows; the first becomes the hub and the rest queue through it.
- 🔒 **Local and locked down**: loopback only, web pages refused, only your pinned extension id may run jobs.
- 💾 **Never overwrites**: files are saved as `name_1.jpg`, `name_2.jpg`, `name_1_2.jpg`…

## Quick start

You need **Python 3.10+**, [**uv**](https://docs.astral.sh/uv/getting-started/installation/), **Google Chrome** (or any
Chromium browser) and a **grok.com account**.

### 1. Add the MCP server to your client

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add -s user grok-web -- uvx --from git+https://github.com/h4775346/grok-web-mcp grok-web-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Edit `claude_desktop_config.json` (Settings → Developer → Edit config):

```json
{
  "mcpServers": {
    "grok-web": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/h4775346/grok-web-mcp", "grok-web-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "grok-web": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/h4775346/grok-web-mcp", "grok-web-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>VS Code (GitHub Copilot)</b></summary>

`.vscode/mcp.json`:

```json
{
  "servers": {
    "grok-web": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/h4775346/grok-web-mcp", "grok-web-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Windsurf, Cline, Zed, others</b></summary>

Any client that runs stdio MCP servers works. The command is:

```
uvx --from git+https://github.com/h4775346/grok-web-mcp grok-web-mcp
```
</details>

### 2. Load the Chrome extension

1. Download `grok-web-mcp-extension.zip` from the [latest release](https://github.com/h4775346/grok-web-mcp/releases/latest)
   and unzip it, or use the [`extension/`](extension) folder of a clone.
2. Open `chrome://extensions`, switch on **Developer mode**, click **Load unpacked** and pick the folder.
3. Pin the extension. Its badge turns green **on** as soon as your MCP client is running.

### 3. Log in to grok.com

Use the same Chrome profile. That's it. Ask your assistant:

> Make two 9:16 images of a neon-lit Cairo rooftop at night with Grok, quality mode, and save them in `rooftop/`.

## Tools

| Tool | What it does |
|---|---|
| `grok_generate_image` | `prompt`, `aspect`, `n` (1–4), `mode` (`speed`/`quality`), `size` or `max_side`, `out_dir`, `name`. Returns the saved file paths. |
| `grok_batch` | A list of image jobs run in order. Refused up front if it does not fit today's budget; jobs over `confirm_over` submissions need `confirm=true`. |
| `grok_generate_video` | *Experimental.* `prompt`, optional `image_path` to animate, `aspect`. Returns the mp4 path. |
| `grok_status` | Is the extension connected, is a grok tab open, are you logged in. |
| `grok_usage` | Today's submissions, images and videos against your limits. |
| `grok_set_limits` | Change the daily limits (the agent is told to ask you first). |

<details>
<summary>Example calls</summary>

```python
grok_generate_image(
    prompt="Cinematic night rooftop in Cairo, warm neon, 35mm",
    aspect="16:9",
    n=2,
    mode="quality",
    out_dir="rooftop",
    size="1344x768",
)

grok_batch(
    out_dir="covers",
    jobs=[
        {"prompt": "minimal album cover, orange sun over the Nile", "name": "cover_a", "aspect": "1:1", "n": 2},
        {"prompt": "retro 80s synthwave album cover", "name": "cover_b", "aspect": "1:1"},
    ],
)

grok_generate_video(
    prompt="slow push-in, hair moving in the wind", image_path="rooftop/grok_1.jpg", aspect="16:9", out_dir="clips"
)
```
</details>

## Quota guard

Every Grok **submission** counts against your plan's Imagine allowance (one image submission returns 2 images; a
video costs much more). The server keeps a ledger in `~/.grok-web-mcp/usage.json` and refuses work beyond
`~/.grok-web-mcp/limits.json`:

```json
{"max_image_submissions_per_day": 30, "max_videos_per_day": 5, "max_jobs_per_batch": 12, "confirm_over": 10}
```

A refusal tells the agent exactly where it stands, for example
`Grok budget: this needs 2 image submission(s) but 29/30 are already used today, so nothing was submitted.`
A job that fails *after* it was submitted (moderation, timeout) still counts, because Grok still charged it.

## Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `GROK_WEB_MCP_OUTPUT_DIR` | `~/grok-web-mcp` | Where relative `out_dir` / `image_path` values resolve. |
| `GROK_WEB_MCP_HOME` | `~/.grok-web-mcp` | Token, usage ledger, limits and pinned extension ids. |
| `GROK_WEB_MCP_PORT` | `8799` | Local WebSocket port (set the same port in the extension popup). |
| `GROK_WEB_MCP_EXTENSION_IDS` | *(empty)* | Comma-separated extension ids allowed in addition to the pinned one. |

Pass them with `-e` in Claude Code (`claude mcp add -e GROK_WEB_MCP_OUTPUT_DIR=~/Pictures/grok ...`) or an `"env"` block in JSON configs.

## Security

- The WebSocket listens on **127.0.0.1 only**.
- Connections from **web pages are refused** (any `http(s)://` Origin), so a site you visit cannot drive your Grok account.
- The **first extension id that connects is pinned** in `~/.grok-web-mcp/extensions.json`; other extensions are refused.
  Reinstalled from a different folder? Edit that file or set `GROK_WEB_MCP_EXTENSION_IDS`.
- Other local processes (a second MCP session) must present a random token from `~/.grok-web-mcp/token.txt`.
- The extension only has access to `https://grok.com/*`. It does not read or send your password or cookies anywhere; it works inside the page you are already logged into, and results go only to the local server.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Troubleshooting

| Message | Fix |
|---|---|
| `no extension connected` | Chrome is closed, or the extension is not loaded. Open Chrome; the badge should say **on**. |
| Badge says **off** | Your MCP client is not running the server, or the ports differ. Check `grok_status` and the popup's port. |
| Badge says **not allowed** | Another extension id was pinned first. Edit `~/.grok-web-mcp/extensions.json`. |
| `not_logged_in` | Log in at grok.com in that Chrome profile. |
| `timeout: no images appeared` | The prompt was moderated, you hit Grok's rate limit, or Grok changed its page. |
| Jobs are slow when the tab is hidden | Keep *Bring the grok tab to front* on in the popup (Chrome throttles hidden tabs). |

Grok's web UI changes from time to time. The selectors live in [`extension/content/main.js`](extension/content/main.js);
if something breaks, please [open an issue](https://github.com/h4775346/grok-web-mcp/issues/new/choose) or send a PR.

## Development

```bash
git clone https://github.com/h4775346/grok-web-mcp && cd grok-web-mcp
uv sync --extra dev
uv run pytest        # end-to-end test with a fake extension: no Chrome, no grok.com, no quota used
uv run ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

This is an independent open-source project. It is **not affiliated with, endorsed by or sponsored by xAI**. "Grok" and
"Grok Imagine" are trademarks of their owners. The extension automates the grok.com page in your own browser
session on your behalf; you are responsible for using it within grok.com's terms of service and your plan's limits.

## License

[MIT](LICENSE)

---

<div align="center">
If this saves you an API bill, a ⭐ helps other people find it.
</div>

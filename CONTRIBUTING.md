# Contributing

Thanks for helping! Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/h4775346/grok-web-mcp && cd grok-web-mcp
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The test suite starts the real server over MCP stdio and talks to it with a **fake extension**, so it needs no
Chrome, no grok.com account and uses no quota. Please extend it when you change server behaviour.

## When Grok changes its page

Most breakages come from grok.com UI changes. The automation lives in `extension/content/main.js`; its selectors
are listed at the top of each helper (`setAspect`, `setImageCount`, `setQuality`, `genVideo`). After editing:

1. click the reload icon on the extension card in `chrome://extensions`,
2. reload the grok.com tab,
3. run `grok_status` and one small `grok_generate_image` call.

Please mention the date you tested and what the page looked like in the PR.

## Ground rules

- Keep the quota guard intact: every real submission must be counted, and nothing may be submitted before a job is validated.
- Never widen the extension's permissions beyond `https://grok.com/*` without discussion.
- Keep the server loopback-only.

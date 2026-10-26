# Changelog

## 0.3.0 (2026-10-26)

First public release.

- MCP tools: `grok_generate_image`, `grok_batch`, `grok_generate_video` (experimental), `grok_status`, `grok_usage`, `grok_set_limits`.
- Chrome MV3 extension that runs jobs in your logged-in grok.com/imagine tab.
- Quota guard: per-day ledger, daily limits, batch pre-check, `confirm_over`.
- Multi-session hub/peer queue.
- Security: loopback only, web origins refused, first extension id pinned (trust on first use), token for local peers.
- Output sizing: exact `size` (cover crop) or `max_side`.
- Installable with `uvx`; config via `GROK_WEB_MCP_*` environment variables.

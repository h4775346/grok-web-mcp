# Changelog

## 0.4.0 (2026-10-26)

- Settings: `~/.grok-web-mcp/config.json` and `GROK_WEB_MCP_<KEY>` environment variables (env wins).
  New keys: `default_aspect`, `default_mode`, `default_n`, `image_timeout_s`, `video_timeout_s`, `quota_guard`,
  `pin_extension`, `extension_ids`, `require_peer_token`, `port`, `output_dir`.
- The local peer token is now optional (`require_peer_token`, on by default) and documented: the extension never needs
  it and users never handle it.
- New tool `grok_settings`; new CLI flags `--init` (writes config.json), `--config` and `--help`.
- Only a `chrome-extension://` origin may take the extension role.
- Invalid settings stop the server at start with a clear message.

## 0.3.0 (2026-10-26)

First public release.

- MCP tools: `grok_generate_image`, `grok_batch`, `grok_generate_video` (experimental), `grok_status`, `grok_usage`, `grok_set_limits`.
- Chrome MV3 extension that runs jobs in your logged-in grok.com/imagine tab.
- Quota guard: per-day ledger, daily limits, batch pre-check, `confirm_over`.
- Multi-session hub/peer queue.
- Security: loopback only, web origins refused, first extension id pinned (trust on first use), token for local peers.
- Output sizing: exact `size` (cover crop) or `max_side`.
- Installable with `uvx`; config via `GROK_WEB_MCP_*` environment variables.

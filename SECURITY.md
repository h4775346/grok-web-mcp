# Security policy

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/h4775346/grok-web-mcp/security/advisories/new) rather than a public issue.

## Threat model

- The server listens on `127.0.0.1` only.
- WebSocket connections carrying a web `Origin` (`http://` or `https://`) are refused, so websites cannot drive your Grok account.
- Chrome extensions are identified by their `chrome-extension://<id>` Origin. The first id that connects is pinned in
  `~/.grok-web-mcp/extensions.json`; any other id is refused.
- Other local clients (a second MCP server process) must present the random token in `~/.grok-web-mcp/token.txt`.
- Local processes running as your user are trusted: anything that can read your home directory can already read the token.

In scope: anything that lets a web page, another extension or a remote host submit jobs, read results or spend quota.

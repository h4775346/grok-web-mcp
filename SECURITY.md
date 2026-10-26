# Security policy

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/h4775346/grok-web-mcp/security/advisories/new) rather than a public issue.

## Threat model

- The server listens on `127.0.0.1` only.
- WebSocket connections carrying a web `Origin` (`http://` or `https://`) are refused, so websites cannot drive your Grok account.
- Chrome extensions are identified by their `chrome-extension://<id>` Origin, and only such an origin may take the
  extension role. With `pin_extension` on (the default) the first id that connects is pinned in
  `~/.grok-web-mcp/extensions.json`; any other id is refused.
- A second grok-web-mcp session forwards its jobs to the first over the local socket. With `require_peer_token` on
  (the default) it must present the random token in `~/.grok-web-mcp/token.txt`, which both sessions read on their own.
  Turning it off lets any local process submit jobs; web pages are still refused.
- Local processes running as your user are otherwise trusted: anything that can read your home directory can already
  read the token.

In scope: anything that lets a web page, another extension or a remote host submit jobs, read results or spend quota.

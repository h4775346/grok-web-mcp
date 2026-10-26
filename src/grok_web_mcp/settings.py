"""Settings: built-in defaults < ~/.grok-web-mcp/config.json < GROK_WEB_MCP_<KEY> environment variables.

Every key below can be set in config.json (same name, lower case) or as an environment variable
(GROK_WEB_MCP_ + the key in upper case, e.g. GROK_WEB_MCP_DEFAULT_ASPECT=9:16). Run `grok-web-mcp --init` to write a
config.json with every key, and `grok-web-mcp --config` to see the values in effect and where each one came from.
"""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("GROK_WEB_MCP_HOME", Path.home() / ".grok-web-mcp")).expanduser()
CONFIG_FILE = HOME / "config.json"

ASPECTS = ("16:9", "9:16", "1:1", "2:3", "3:2")
MODES = ("", "speed", "quality")

# key: (default, help)
OPTIONS = {
    "port": (8799, "Local WebSocket port shared by the server and the Chrome extension (set the same port in the popup)."),
    "output_dir": ("~/grok-web-mcp", "Folder that relative out_dir / image_path values resolve against."),
    "default_aspect": ("16:9", "Aspect ratio when a call does not pass one: 16:9, 9:16, 1:1, 2:3 or 3:2."),
    "default_mode": ("", 'Speed/Quality when a call does not pass one: "speed", "quality" or "" (leave the page as it is).'),
    "default_n": (1, "Images to keep per call when a call does not pass n (1-4; every 2 images = 1 Grok submission)."),
    "image_timeout_s": (180, "Seconds to wait for an image submission to finish."),
    "video_timeout_s": (420, "Seconds to wait for a video to finish."),
    "quota_guard": (True, "Refuse jobs over the daily limits in limits.json. Usage is always recorded, even when off."),
    "pin_extension": (True, "Trust only the first Chrome extension id that connects (stored in extensions.json)."),
    "extension_ids": ([], "Extension ids always allowed, in addition to the pinned one (env: comma-separated)."),
    "require_peer_token": (
        True,
        "A second MCP session forwards its jobs to the first one over the local socket. With this on, it must present "
        "the random token in token.txt (both sessions read it automatically), so no other program on this computer "
        "can submit jobs to your Grok account. Turn off only if you trust every local process.",
    ),
}
DEFAULTS = {k: v[0] for k, v in OPTIONS.items()}
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


class SettingsError(ValueError):
    pass


def _coerce(key: str, value, source: str):
    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            if isinstance(value, bool):
                return value
            v = str(value).strip().lower()
            if v in _TRUE:
                return True
            if v in _FALSE:
                return False
            raise ValueError
        if isinstance(default, int):
            return int(value)
        if isinstance(default, list):
            if isinstance(value, list):
                return [str(x).strip() for x in value if str(x).strip()]
            return [s.strip() for s in str(value).split(",") if s.strip()]
        return str(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} from {source}: cannot read {value!r} as {type(default).__name__}") from None


def _validate(s: dict):
    if not 1024 <= s["port"] <= 65535:
        raise SettingsError("port must be 1024..65535")
    if s["default_aspect"] not in ASPECTS:
        raise SettingsError(f"default_aspect must be one of {', '.join(ASPECTS)}")
    s["default_mode"] = s["default_mode"].lower()
    if s["default_mode"] not in MODES:
        raise SettingsError('default_mode must be "speed", "quality" or ""')
    if not 1 <= s["default_n"] <= 4:
        raise SettingsError("default_n must be 1..4")
    for k in ("image_timeout_s", "video_timeout_s"):
        if not 10 <= s[k] <= 3600:
            raise SettingsError(f"{k} must be 10..3600")


def load() -> tuple[dict, dict]:
    """Returns (settings, source of each key)."""
    s, src = dict(DEFAULTS), {k: "default" for k in DEFAULTS}
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SettingsError(f"{CONFIG_FILE} is not valid JSON: {e}") from None
        for k, v in (raw if isinstance(raw, dict) else {}).items():
            if k in DEFAULTS:
                s[k], src[k] = _coerce(k, v, str(CONFIG_FILE)), "config.json"
    for k in DEFAULTS:
        env = os.environ.get("GROK_WEB_MCP_" + k.upper())
        if env is not None and env != "":
            s[k], src[k] = _coerce(k, env, "GROK_WEB_MCP_" + k.upper()), "env"
    _validate(s)
    return s, src


def write_template() -> bool:
    """Write config.json with every key at its default (only when it does not exist yet)."""
    if CONFIG_FILE.exists():
        return False
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    return True

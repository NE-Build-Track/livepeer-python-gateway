"""Validate application headers without allowing payment or HTTP framing overrides."""

import re

_RESERVED = {
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
    "host", "connection", "keep-alive", "proxy-connection", "te", "trailer", "upgrade",
    "transfer-encoding", "content-length", "content-encoding", "content-type", "expect",
    "accept-encoding",
}


def _runner_headers(headers: dict[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    size = 0
    for name, value in (headers or {}).items():
        name = name.lower()
        if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", name)
            or name in _RESERVED or name.startswith("livepeer-") or name in result):
            raise ValueError("Reserved, duplicate or invalid runner header")
        if any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("Invalid runner header value")
        size += len(name) + len(value)
        if len(result) >= 32 or size > 16384:
            raise ValueError("Runner headers exceed limit")
        result[name] = value
    return result

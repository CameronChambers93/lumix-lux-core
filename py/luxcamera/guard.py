"""Safety guard: nothing that deletes from a camera or changes its settings is ever sent, whatever a driver says.

Same rules as js/lux-camera.js: a request is refused when a word of its path or query (split on anything but letters,
digits and _) starts with "del" or contains "delete", or starts with "format", "setsetting" or "setinfo".
POSTs must be a SOAP Browse or Search with no write action in the body."""
from __future__ import annotations

import re
from urllib.parse import unquote_plus

DENY = [re.compile(p) for p in (r"^del", r"delete", r"^format", r"^setsetting", r"^setinfo")]
SOAP_ALLOWED = re.compile(r'#(Browse|Search)"?$')
SOAP_DENIED_BODY = re.compile(r"<\s*(?:[\w-]+:)?(Destroy|Delete|Update|Create|Import|Export|Move)\w*[\s/>]", re.I)


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(f"Refused to send: {reason}")
        self.reason = reason


def deny_reason(url: str, method: str = "GET", body: str = "", headers: dict | None = None):
    target = unquote_plus(re.sub(r"^[a-z]+://[^/]*", "", str(url), flags=re.I))
    for word in re.split(r"[^a-z0-9_]+", target.lower()):
        if word and any(p.search(word) for p in DENY):
            return f'"{word}" is on the denylist'
    if method not in ("GET", "HEAD"):
        action = next((v for k, v in (headers or {}).items() if k.lower() == "soapaction"), "")
        if not SOAP_ALLOWED.search(action):
            return f"{method} is only allowed for a SOAP Browse or Search"
        if SOAP_DENIED_BODY.search(body or ""):
            return "the SOAP body has a write action"
    return None


def guard(url: str, method: str = "GET", body: str = "", headers: dict | None = None) -> None:
    why = deny_reason(url, method, body, headers)
    if why:
        raise Refused(why)

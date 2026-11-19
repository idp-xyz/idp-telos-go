"""Signed references to search results (ADR-0003 D2).

A search result carries a ref: "<expiry, 8 hex digits>.<HMAC-SHA256(key, caller | url | expiry), 16 base32 chars>".
web_fetch takes the url and its ref and reads the url only when the ref was made for that url, that caller, and has
not expired. A url the model writes itself (with a secret in its path, say) has no valid ref: it cannot be read.
Stateless, so that every replica of idp-web verifies what any other made.
"""
import base64
import hashlib
import hmac
import time

MAC_CHARS = 16  # 80 bits


def _mac(key: bytes, caller: str, url: str, expiry: int) -> str:
    msg = b"\x00".join([caller.encode(), url.encode(), str(expiry).encode()])
    return base64.b32encode(hmac.new(key, msg, hashlib.sha256).digest()).decode().lower()[:MAC_CHARS]


def make(key: bytes, caller: str, url: str, ttl: int = 3600, now: float | None = None) -> str:
    expiry = int((time.time() if now is None else now) + ttl)
    return f"{expiry:08x}.{_mac(key, caller, url, expiry)}"


def check(key: bytes, caller: str, url: str, ref: str, now: float | None = None) -> str | None:
    """None when the ref is good for this url and caller; otherwise why not."""
    try:
        exp_hex, mac = ref.strip().split(".", 1)
        expiry = int(exp_hex, 16)
    except (ValueError, AttributeError):
        return "not a ref"
    if not hmac.compare_digest(mac, _mac(key, caller, url, expiry)):
        return "the ref was not made for this address"
    if (time.time() if now is None else now) > expiry:
        return "the ref has expired"
    return None

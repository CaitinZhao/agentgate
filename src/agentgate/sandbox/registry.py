"""Token -> sandbox registry behind the HTTP exec API.

The agent never receives provider handles — only an unguessable per-sandbox token
it presents at POST /api/v1/sandbox/exec. Tokens expire (TTL) and are unregistered
when the run loop closes the sandbox, so an agent cannot touch a previous case's
environment.
"""
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

_lock = threading.Lock()
_by_token: Dict[str, Tuple[float, object]] = {}      # token -> (deadline, handle)


def register(handle, ttl_s: float = 1800.0) -> str:
    token = uuid.uuid4().hex
    with _lock:
        _by_token[token] = (time.time() + max(1.0, float(ttl_s)), handle)
    return token


def resolve(token: str) -> Optional[object]:
    with _lock:
        entry = _by_token.get(str(token or ""))
        if not entry:
            return None
        deadline, handle = entry
        if time.time() > deadline:
            _by_token.pop(str(token or ""), None)
            return None
        return handle


def unregister(token: str) -> None:
    with _lock:
        _by_token.pop(str(token or ""), None)

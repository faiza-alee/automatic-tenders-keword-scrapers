import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API key issued to you")


def _matches(provided: str | None, valid: set[str]) -> bool:
    if not provided:
        return False
    return any(hmac.compare_digest(provided.encode(), k.encode()) for k in valid)  # constant-time


def require_api_key(request: Request, key: str | None = Security(api_key_header)) -> str:
    s = request.app.state.settings
    if _matches(key, s.api_key_set | s.admin_key_set):
        return key
    raise HTTPException(401, "Missing or invalid API key (send it in the X-API-Key header)")


def require_admin(request: Request, key: str | None = Security(api_key_header)) -> str:
    s = request.app.state.settings
    if _matches(key, s.admin_key_set):
        return key
    if _matches(key, s.api_key_set):
        raise HTTPException(403, "This endpoint requires an admin API key")
    raise HTTPException(401, "Missing or invalid API key (send it in the X-API-Key header)")


class RateLimiter:
    """Sliding-window limiter, per API key (or client IP). In-memory: with several API worker processes
    each has its own window; put Redis behind it (e.g. slowapi + redis) if you need a global limit."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, identity: str) -> int | None:
        """Returns None if allowed, else seconds until a slot frees up."""
        now = time.monotonic()
        q = self.hits[identity]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return max(1, int(60 - (now - q[0])) + 1)
        q.append(now)
        return None

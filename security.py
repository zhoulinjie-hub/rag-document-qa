from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path


class DemoSecurityConfigurationError(RuntimeError):
    pass


class DemoAuthenticationError(ValueError):
    pass


class DemoRateLimitError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("请求过于频繁，请稍后重试")
        self.retry_after = max(1, retry_after)


@dataclass(frozen=True)
class DemoSession:
    session_id: str
    expires_at: int


class SlidingWindowLimiter:
    """适用于单进程面试演示的内存滑动窗口限流器。"""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = int(events[0] + window_seconds - now) + 1
                raise DemoRateLimitError(retry_after)
            events.append(now)


class DemoSecurity:
    def __init__(
        self,
        access_code: str,
        session_ttl_minutes: int = 240,
    ) -> None:
        if len(access_code) < 16:
            raise DemoSecurityConfigurationError(
                "演示访问码长度至少需要 16 个字符"
            )
        self._access_code = access_code
        self._signing_key = hashlib.sha256(access_code.encode("utf-8")).digest()
        self.session_ttl_seconds = max(15, session_ttl_minutes) * 60
        self.rate_limiter = SlidingWindowLimiter()

    @classmethod
    def from_environment(cls, project_dir: Path) -> DemoSecurity:
        access_code = os.getenv("DEMO_ACCESS_TOKEN", "").strip()
        if not access_code:
            token_path = project_dir / "data" / "demo_access_token.txt"
            if token_path.exists():
                access_code = token_path.read_text(encoding="utf-8").strip()
            else:
                token_path.parent.mkdir(parents=True, exist_ok=True)
                access_code = secrets.token_urlsafe(24)
                temporary_path = token_path.with_suffix(".txt.tmp")
                temporary_path.write_text(access_code, encoding="utf-8")
                os.replace(temporary_path, token_path)
                try:
                    token_path.chmod(0o600)
                except OSError:
                    pass

        ttl = int(os.getenv("DEMO_SESSION_TTL_MINUTES", "240"))
        return cls(access_code=access_code, session_ttl_minutes=ttl)

    @staticmethod
    def _encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def create_session(self, supplied_access_code: str) -> tuple[str, DemoSession]:
        if not hmac.compare_digest(supplied_access_code, self._access_code):
            raise DemoAuthenticationError("访问码不正确")

        session = DemoSession(
            session_id=secrets.token_urlsafe(18),
            expires_at=int(time.time()) + self.session_ttl_seconds,
        )
        payload = json.dumps(
            {"sid": session.session_id, "exp": session.expires_at},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded_payload = self._encode(payload)
        signature = hmac.new(
            self._signing_key,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{self._encode(signature)}", session

    def verify_session(self, token: str) -> DemoSession:
        try:
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
            supplied_signature = self._decode(encoded_signature)
            expected_signature = hmac.new(
                self._signing_key,
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise DemoAuthenticationError("登录状态无效")

            payload = json.loads(self._decode(encoded_payload))
            session = DemoSession(
                session_id=str(payload["sid"]),
                expires_at=int(payload["exp"]),
            )
        except DemoAuthenticationError:
            raise
        except Exception as exc:
            raise DemoAuthenticationError("登录状态无效") from exc

        if session.expires_at <= int(time.time()):
            raise DemoAuthenticationError("登录状态已过期")
        if not session.session_id:
            raise DemoAuthenticationError("登录状态无效")
        return session


"""
门户登录态（签名 token）

和「对话上下文」的 SessionStore 完全是两码事：
- 这里管的是「谁登录了、登录没过期」（门户层，有状态属于用户会话）。
- SessionStore 管的是「某次对话的多轮历史」（内核层，无状态由调用方给 key）。

实现用 HMAC 签名的无状态 token（不需要服务端存储）：
    token = base64url(payload_json) + "." + base64url(hmac_sha256(payload))
payload 里有用户信息和过期时间。校验时重算签名并查过期。

无状态的取舍：简单、天然支持多进程（都用同一个 secret 即可），但没法主动
「踢下线」。将来要吊销，可加一个服务端的 token 黑名单（接口已可扩展）。
secret 必须保密且稳定：多进程/重启要用同一个，否则已发的 token 全失效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from botkit.portal.auth.base import AuthUser

logger = logging.getLogger("botkit.portal.sessions")

# 默认登录态有效期（秒）。12 小时，一个工作日内不用重复登录。
DEFAULT_TTL_SECONDS = 12 * 3600


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class TokenSigner:
    """
    签发 / 校验登录态 token。

    secret 是签名密钥，必须保密且在多进程/重启间保持一致（否则旧 token 失效）。
    门户启动时应从 .env（如 PORTAL_SECRET）读取；没配时可临时随机生成一个，
    但那样每次重启都会让所有人重新登录——生产务必显式配置。
    """

    def __init__(self, secret: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        if not secret:
            raise ValueError("TokenSigner 需要一个非空 secret")
        self._secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def _sign(self, payload_b64: str) -> str:
        mac = hmac.new(self._secret, payload_b64.encode("ascii"), hashlib.sha256)
        return _b64url_encode(mac.digest())

    def issue(self, user: AuthUser, *, now: float | None = None) -> str:
        """给一个已认证用户签发 token。"""
        issued = int(now if now is not None else time.time())
        payload = {
            "username": user.username,
            "identity": user.identity,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
            "exp": issued + self.ttl_seconds,
        }
        payload_b64 = _b64url_encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        return f"{payload_b64}.{self._sign(payload_b64)}"

    def verify(self, token: str, *, now: float | None = None) -> AuthUser | None:
        """
        校验 token。签名不符 / 过期 / 格式坏一律返回 None（登录态无效）。
        """
        if not token or "." not in token:
            return None
        payload_b64, _, sig = token.partition(".")
        if not payload_b64 or not sig:
            return None

        # 常量时间比对签名，防伪造与时序侧信道
        expected = self._sign(payload_b64)
        if not hmac.compare_digest(expected, sig):
            return None

        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        exp = payload.get("exp")
        current = now if now is not None else time.time()
        if not isinstance(exp, (int, float)) or current >= exp:
            return None

        username = payload.get("username")
        if not username:
            return None

        return AuthUser(
            username=str(username),
            identity=str(payload.get("identity") or username),
            display_name=str(payload.get("display_name") or ""),
            is_admin=bool(payload.get("is_admin", False)),
        )

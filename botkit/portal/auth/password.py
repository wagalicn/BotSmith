"""
账号密码认证（内置的第一个 AuthProvider）

查 UserStore 取用户，用 verify_password 校验。密码错 / 用户不存在都返回 None
（不区分，避免暴露哪些用户名存在）。
"""

from __future__ import annotations

import logging

from botkit.portal.auth.base import AuthProvider, AuthUser
from botkit.portal.users import UserStore, verify_password

logger = logging.getLogger("botkit.portal.auth")


class PasswordAuthProvider(AuthProvider):
    def __init__(self, user_store: UserStore):
        self.users = user_store

    async def authenticate(self, username: str, password: str) -> AuthUser | None:
        username = (username or "").strip()
        if not username or not password:
            return None

        rec = self.users.get(username)
        if rec is None:
            # 不存在的用户也走一遍假校验，让耗时和「存在但密码错」接近，
            # 不给出「这个用户名是否存在」的时序线索。
            verify_password(password, "0" * 64, "00")
            logger.info("登录失败：用户不存在 username=%s", username)
            return None

        if not verify_password(password, rec.password_hash, rec.salt):
            logger.info("登录失败：密码错误 username=%s", username)
            return None

        return AuthUser(
            username=rec.username,
            identity=rec.username,  # 账号即工号
            display_name=rec.display_name,
            is_admin=rec.is_admin,
        )

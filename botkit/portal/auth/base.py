"""
认证接口

AuthProvider.authenticate 成功返回 AuthUser，失败返回 None。门户据此发登录态。
account = 工号：AuthUser.identity 就是传给 /chat 的 user。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AuthUser:
    """
    认证通过后的用户身份（和具体鉴权方式无关）。

    username   登录名。本框架约定账号=工号。
    identity   传给 /chat 的 user。默认等于 username（账号即工号）；
               将来若登录名与工号分离（如邮箱登录），由 provider 填不同的值。
    """

    username: str
    identity: str = ""
    display_name: str = ""
    is_admin: bool = False

    def __post_init__(self) -> None:
        # 账号即工号：没显式给 identity 时用 username
        if not self.identity:
            self.identity = self.username


class AuthProvider(ABC):
    """
    鉴权接口。门户登录时调用，具体怎么验证（密码 / SSO / LDAP）由实现决定。
    """

    @abstractmethod
    async def authenticate(self, username: str, password: str) -> AuthUser | None:
        """
        验证凭据。成功返回 AuthUser，失败（用户不存在 / 密码错 / 被禁用等）
        返回 None。实现不应因「密码错」抛异常——那是正常的登录失败。
        """
        ...

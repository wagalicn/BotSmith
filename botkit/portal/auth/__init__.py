"""
门户认证层（可插拔）

AuthProvider 是鉴权的统一接口。内置账号密码实现（PasswordAuthProvider）；
将来接 SSO / LDAP / OIDC，各写一个 AuthProvider 实现即可，门户其余部分不用改。
"""

from botkit.portal.auth.base import AuthProvider, AuthUser
from botkit.portal.auth.password import PasswordAuthProvider

__all__ = ["AuthProvider", "AuthUser", "PasswordAuthProvider"]

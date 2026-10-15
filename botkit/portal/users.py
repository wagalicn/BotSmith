"""
门户用户存储与密码哈希

- 密码只存哈希 + 每用户随机 salt，绝不明文；用标准库 pbkdf2_hmac，不加依赖。
- UserStore 抽象成接口（和 SessionStore 一个套路），内置 JsonUserStore 存
  users.json；将来要换数据库，实现同一接口即可，上层不感知。
- 账号 = 工号：username 本身就是传给 /chat 的 user，不额外存一份身份。

users.json 像 .env 一样含敏感信息（密码哈希），不进 git。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("botkit.portal.users")

# pbkdf2 迭代次数。够慢以抵御离线爆破，又不至于让登录明显卡顿。
_PBKDF2_ROUNDS = 200_000
_SALT_BYTES = 16


class UserError(Exception):
    """用户操作失败（重名、找不到用户等），消息可直接给用户看。"""


# --------------------------------------------------------------------------
# 密码哈希（纯标准库）
# --------------------------------------------------------------------------


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    """
    计算密码哈希。返回 (hash_hex, salt_hex)。

    不传 salt 时生成一个新的随机 salt（建用户 / 改密码用）；
    传 salt 时用它复算（校验用）。
    """
    if not password:
        raise UserError("密码不能为空")
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, _PBKDF2_ROUNDS
    )
    return digest.hex(), salt_bytes.hex()


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """常量时间比较，防时序侧信道。密码/盐缺失一律判为不通过。"""
    if not password or not hash_hex or not salt_hex:
        return False
    try:
        calc, _ = hash_password(password, salt=salt_hex)
    except (ValueError, UserError):
        return False
    return hmac.compare_digest(calc, hash_hex)


# --------------------------------------------------------------------------
# 用户记录
# --------------------------------------------------------------------------


@dataclass
class UserRecord:
    """一个门户用户。username 即工号。"""

    username: str
    password_hash: str
    salt: str
    display_name: str = ""
    is_admin: bool = False

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "salt": self.salt,
            "display_name": self.display_name,
            "is_admin": self.is_admin,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserRecord":
        return cls(
            username=str(data["username"]),
            password_hash=str(data.get("password_hash", "")),
            salt=str(data.get("salt", "")),
            display_name=str(data.get("display_name", "")),
            is_admin=bool(data.get("is_admin", False)),
        )


# --------------------------------------------------------------------------
# 用户存储接口
# --------------------------------------------------------------------------


class UserStore(ABC):
    """
    门户用户存储接口。内置 JsonUserStore；将来可加数据库实现。

    上层（AuthProvider、门户管理页）只依赖这个接口，不感知具体后端。
    """

    @abstractmethod
    def get(self, username: str) -> UserRecord | None:
        """按用户名取用户，不存在返回 None。"""
        ...

    @abstractmethod
    def list_users(self) -> list[UserRecord]:
        """列出所有用户（不含密码明文——本来也只有哈希）。"""
        ...

    @abstractmethod
    def create(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "",
        is_admin: bool = False,
    ) -> UserRecord:
        """建用户。用户名已存在时抛 UserError。"""
        ...

    @abstractmethod
    def set_password(self, username: str, password: str) -> None:
        """改密码。用户不存在时抛 UserError。"""
        ...

    @abstractmethod
    def delete(self, username: str) -> None:
        """删用户。用户不存在时抛 UserError。"""
        ...


class JsonUserStore(UserStore):
    """
    用一个 JSON 文件存用户的实现。进程内不缓存，每次操作都读/写文件，
    这样多进程（网关多开）看到的是同一份数据——对门户这种低频写入够用。

    文件形如：{"users": [{username, password_hash, salt, display_name, is_admin}, ...]}
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # -- 文件读写 ----------------------------------------------------------

    def _load(self) -> dict[str, UserRecord]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise UserError(f"用户文件读取失败：{e}") from e
        users: dict[str, UserRecord] = {}
        for item in raw.get("users", []):
            try:
                rec = UserRecord.from_dict(item)
            except (KeyError, TypeError):
                logger.warning("跳过一条格式不对的用户记录：%r", item)
                continue
            users[rec.username] = rec
        return users

    def _save(self, users: dict[str, UserRecord]) -> None:
        data = {"users": [u.to_dict() for u in users.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换，避免写一半进程挂了把用户表写坏
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    # -- 接口实现 ----------------------------------------------------------

    def get(self, username: str) -> UserRecord | None:
        return self._load().get(username)

    def list_users(self) -> list[UserRecord]:
        return sorted(self._load().values(), key=lambda u: u.username)

    def create(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "",
        is_admin: bool = False,
    ) -> UserRecord:
        username = (username or "").strip()
        if not username:
            raise UserError("用户名（工号）不能为空")
        users = self._load()
        if username in users:
            raise UserError(f"用户名已存在：{username}")
        pw_hash, salt = hash_password(password)
        rec = UserRecord(
            username=username,
            password_hash=pw_hash,
            salt=salt,
            display_name=display_name,
            is_admin=is_admin,
        )
        users[username] = rec
        self._save(users)
        return rec

    def set_password(self, username: str, password: str) -> None:
        users = self._load()
        rec = users.get(username)
        if rec is None:
            raise UserError(f"用户不存在：{username}")
        rec.password_hash, rec.salt = hash_password(password)
        self._save(users)

    def delete(self, username: str) -> None:
        users = self._load()
        if username not in users:
            raise UserError(f"用户不存在：{username}")
        del users[username]
        self._save(users)

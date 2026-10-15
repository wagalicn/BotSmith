"""门户认证地基测试：密码哈希 / 用户存储 / 账号密码 provider / 登录态 token"""

from __future__ import annotations

from pathlib import Path

import pytest

from botkit.portal.auth import AuthUser, PasswordAuthProvider
from botkit.portal.sessions import TokenSigner
from botkit.portal.users import (
    JsonUserStore,
    UserError,
    hash_password,
    verify_password,
)


# --------------------------------------------------------------------------
# 密码哈希
# --------------------------------------------------------------------------


def test_hash_password_roundtrip():
    h, salt = hash_password("hunter2")
    assert verify_password("hunter2", h, salt) is True


def test_wrong_password_fails():
    h, salt = hash_password("hunter2")
    assert verify_password("wrong", h, salt) is False


def test_same_password_different_salt_differs():
    h1, s1 = hash_password("same")
    h2, s2 = hash_password("same")
    assert s1 != s2
    assert h1 != h2  # salt 不同 → 哈希不同


def test_hash_is_deterministic_with_fixed_salt():
    h1, salt = hash_password("pw")
    h2, _ = hash_password("pw", salt=salt)
    assert h1 == h2


def test_empty_password_rejected():
    with pytest.raises(UserError):
        hash_password("")


def test_verify_rejects_missing_parts():
    assert verify_password("", "x", "y") is False
    assert verify_password("pw", "", "y") is False
    assert verify_password("pw", "x", "") is False


# --------------------------------------------------------------------------
# JsonUserStore
# --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> JsonUserStore:
    return JsonUserStore(tmp_path / "users.json")


def test_create_and_get(store):
    store.create("L220104", "pw", display_name="张三", is_admin=True)
    rec = store.get("L220104")
    assert rec is not None
    assert rec.username == "L220104"
    assert rec.display_name == "张三"
    assert rec.is_admin is True
    # 存的是哈希，不是明文
    assert rec.password_hash and rec.password_hash != "pw"
    assert verify_password("pw", rec.password_hash, rec.salt)


def test_get_unknown_returns_none(store):
    assert store.get("nope") is None


def test_create_duplicate_rejected(store):
    store.create("L220104", "pw")
    with pytest.raises(UserError, match="已存在"):
        store.create("L220104", "other")


def test_create_blank_username_rejected(store):
    with pytest.raises(UserError, match="不能为空"):
        store.create("  ", "pw")


def test_persists_across_instances(tmp_path):
    path = tmp_path / "users.json"
    JsonUserStore(path).create("L220104", "pw", display_name="张三")
    # 另起一个实例读同一个文件
    rec = JsonUserStore(path).get("L220104")
    assert rec is not None and rec.display_name == "张三"


def test_set_password(store):
    store.create("L220104", "old")
    store.set_password("L220104", "new")
    rec = store.get("L220104")
    assert verify_password("new", rec.password_hash, rec.salt)
    assert not verify_password("old", rec.password_hash, rec.salt)


def test_set_password_unknown_user(store):
    with pytest.raises(UserError, match="不存在"):
        store.set_password("nope", "x")


def test_delete(store):
    store.create("L220104", "pw")
    store.delete("L220104")
    assert store.get("L220104") is None


def test_delete_unknown_user(store):
    with pytest.raises(UserError, match="不存在"):
        store.delete("nope")


def test_list_users_sorted(store):
    store.create("M330000", "pw")
    store.create("L220104", "pw")
    assert [u.username for u in store.list_users()] == ["L220104", "M330000"]


def test_empty_store_lists_nothing(store):
    assert store.list_users() == []


# --------------------------------------------------------------------------
# PasswordAuthProvider
# --------------------------------------------------------------------------


async def test_authenticate_success(store):
    store.create("L220104", "pw", display_name="张三")
    provider = PasswordAuthProvider(store)
    user = await provider.authenticate("L220104", "pw")
    assert user is not None
    assert user.username == "L220104"
    assert user.identity == "L220104"  # 账号即工号
    assert user.display_name == "张三"


async def test_authenticate_wrong_password(store):
    store.create("L220104", "pw")
    provider = PasswordAuthProvider(store)
    assert await provider.authenticate("L220104", "bad") is None


async def test_authenticate_unknown_user(store):
    provider = PasswordAuthProvider(store)
    assert await provider.authenticate("ghost", "pw") is None


async def test_authenticate_blank_rejected(store):
    provider = PasswordAuthProvider(store)
    assert await provider.authenticate("", "pw") is None
    assert await provider.authenticate("L220104", "") is None


async def test_authenticate_admin_flag(store):
    store.create("admin", "pw", is_admin=True)
    provider = PasswordAuthProvider(store)
    user = await provider.authenticate("admin", "pw")
    assert user.is_admin is True


# --------------------------------------------------------------------------
# AuthUser
# --------------------------------------------------------------------------


def test_authuser_identity_defaults_to_username():
    u = AuthUser(username="L220104")
    assert u.identity == "L220104"


def test_authuser_identity_can_differ():
    u = AuthUser(username="zhang@corp.com", identity="L220104")
    assert u.identity == "L220104"


# --------------------------------------------------------------------------
# TokenSigner（登录态）
# --------------------------------------------------------------------------


def test_token_issue_and_verify():
    signer = TokenSigner("secret-key")
    user = AuthUser(username="L220104", display_name="张三", is_admin=True)
    token = signer.issue(user)
    back = signer.verify(token)
    assert back is not None
    assert back.username == "L220104"
    assert back.identity == "L220104"
    assert back.display_name == "张三"
    assert back.is_admin is True


def test_token_expired():
    signer = TokenSigner("secret-key", ttl_seconds=100)
    user = AuthUser(username="L220104")
    token = signer.issue(user, now=1000)
    # 过期后（now 超过 issued+ttl）校验失败
    assert signer.verify(token, now=1000 + 101) is None
    # 有效期内通过
    assert signer.verify(token, now=1000 + 50) is not None


def test_token_tampered_payload_fails():
    signer = TokenSigner("secret-key")
    token = signer.issue(AuthUser(username="L220104"))
    payload_b64, _, sig = token.partition(".")
    # 换一个 payload，签名对不上
    forged = payload_b64[:-2] + "AA" + "." + sig
    assert signer.verify(forged) is None


def test_token_wrong_secret_fails():
    token = TokenSigner("secret-A").issue(AuthUser(username="L220104"))
    assert TokenSigner("secret-B").verify(token) is None


def test_token_garbage_rejected():
    signer = TokenSigner("secret-key")
    assert signer.verify("") is None
    assert signer.verify("not-a-token") is None
    assert signer.verify("a.b.c") is None


def test_signer_requires_secret():
    with pytest.raises(ValueError):
        TokenSigner("")

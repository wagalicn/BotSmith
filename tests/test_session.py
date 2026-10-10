"""会话存储与隔离策略测试"""

from __future__ import annotations

import pytest

from botkit.config import SessionConfig
from botkit.session import InMemorySessionStore, SessionStore, build_session_key


def frame(userid: str, chatid: str = "", chattype: str = "single") -> dict:
    return {
        "body": {
            "from": {"userid": userid},
            "chatid": chatid,
            "chattype": chattype,
            "text": {"content": "hi"},
        }
    }


class FakeClock:
    """可手动推进的假时钟，避免测 TTL 时真的 sleep"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------
# scope 隔离策略
# --------------------------------------------------------------------------


def test_group_user_scope_isolates_people_in_same_group():
    g = frame("L220104", chatid="chat-1", chattype="group")
    h = frame("L220105", chatid="chat-1", chattype="group")
    assert build_session_key(g, "group_user") != build_session_key(h, "group_user")


def test_group_user_scope_isolates_same_person_across_groups():
    a = frame("L220104", chatid="chat-1", chattype="group")
    b = frame("L220104", chatid="chat-2", chattype="group")
    assert build_session_key(a, "group_user") != build_session_key(b, "group_user")


def test_group_scope_shares_context_within_group():
    g = frame("L220104", chatid="chat-1", chattype="group")
    h = frame("L220105", chatid="chat-1", chattype="group")
    assert build_session_key(g, "group") == build_session_key(h, "group")


def test_group_scope_separates_different_groups():
    a = frame("L220104", chatid="chat-1", chattype="group")
    b = frame("L220104", chatid="chat-2", chattype="group")
    assert build_session_key(a, "group") != build_session_key(b, "group")


def test_user_scope_shares_across_groups_and_single():
    in_group = frame("L220104", chatid="chat-1", chattype="group")
    in_single = frame("L220104", chattype="single")
    other_group = frame("L220104", chatid="chat-9", chattype="group")
    assert (
        build_session_key(in_group, "user")
        == build_session_key(in_single, "user")
        == build_session_key(other_group, "user")
    )


def test_user_scope_still_separates_different_people():
    a = frame("L220104", chatid="chat-1", chattype="group")
    b = frame("L220105", chatid="chat-1", chattype="group")
    assert build_session_key(a, "user") != build_session_key(b, "user")


def test_single_chat_is_per_person_in_all_scopes():
    a = frame("L220104", chattype="single")
    b = frame("L220105", chattype="single")
    for scope in ("group_user", "group", "user"):
        assert build_session_key(a, scope) != build_session_key(b, scope)


def test_group_without_chatid_falls_back_to_per_person():
    """拿不到 chatid 时退化成按人隔离，不能把不同群混成同一个会话"""
    a = frame("L220104", chatid="", chattype="group")
    b = frame("L220105", chatid="", chattype="group")
    assert build_session_key(a, "group_user") != build_session_key(b, "group_user")
    assert build_session_key(a, "group") != build_session_key(b, "group")


def test_store_key_for_uses_its_own_scope():
    store = InMemorySessionStore(scope="group")
    g = frame("L220104", chatid="chat-1", chattype="group")
    h = frame("L220105", chatid="chat-1", chattype="group")
    assert store.key_for(g) == store.key_for(h)


# --------------------------------------------------------------------------
# 历史读写
# --------------------------------------------------------------------------


def test_append_and_get_history():
    store = InMemorySessionStore()
    store.append_turn("k", "你好", "你好，有什么可以帮你")
    history = store.get_history("k")
    assert history == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你"},
    ]


def test_empty_history_for_unknown_key():
    assert InMemorySessionStore().get_history("nope") == []


def test_history_is_a_copy():
    """调用方改返回值不能污染内部状态"""
    store = InMemorySessionStore()
    store.append_turn("k", "a", "b")
    history = store.get_history("k")
    history.append({"role": "user", "content": "注入"})
    history[0]["content"] = "改掉了"
    assert store.get_history("k") == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]


def test_sessions_do_not_leak_into_each_other():
    store = InMemorySessionStore()
    store.append_turn("k1", "问题1", "回答1")
    store.append_turn("k2", "问题2", "回答2")
    assert store.get_history("k1")[0]["content"] == "问题1"
    assert store.get_history("k2")[0]["content"] == "问题2"


# --------------------------------------------------------------------------
# max_turns 截断
# --------------------------------------------------------------------------


def test_max_turns_truncates_oldest():
    store = InMemorySessionStore(max_turns=2)
    for i in range(5):
        store.append_turn("k", f"q{i}", f"a{i}")

    history = store.get_history("k")
    assert len(history) == 4  # 2 轮 = 4 条消息
    assert history[0]["content"] == "q3"  # 最早的被丢掉
    assert history[-1]["content"] == "a4"


def test_turn_count():
    store = InMemorySessionStore(max_turns=3)
    assert store.turn_count("k") == 0
    store.append_turn("k", "q", "a")
    assert store.turn_count("k") == 1
    for i in range(5):
        store.append_turn("k", f"q{i}", f"a{i}")
    assert store.turn_count("k") == 3  # 被 max_turns 压住


def test_history_alternates_user_assistant_after_truncation():
    """截断后第一条必须还是 user，否则有些 LLM 会报错"""
    store = InMemorySessionStore(max_turns=2)
    for i in range(6):
        store.append_turn("k", f"q{i}", f"a{i}")
    history = store.get_history("k")
    assert [m["role"] for m in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


# --------------------------------------------------------------------------
# TTL 过期
# --------------------------------------------------------------------------


def test_history_expires_after_ttl():
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=1800, clock=clock)
    store.append_turn("k", "q", "a")
    assert store.get_history("k") != []

    clock.advance(1801)
    assert store.get_history("k") == []


def test_history_survives_within_ttl():
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=1800, clock=clock)
    store.append_turn("k", "q", "a")
    clock.advance(1799)
    assert store.get_history("k") != []


def test_activity_refreshes_ttl():
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=100, clock=clock)
    store.append_turn("k", "q1", "a1")
    clock.advance(80)
    store.append_turn("k", "q2", "a2")  # 刷新 last_active
    clock.advance(80)
    # 距首次 160 秒但距最后活动只有 80 秒，应该还活着
    assert len(store.get_history("k")) == 4


def test_expired_sessions_are_removed_from_memory():
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=10, clock=clock)
    for i in range(5):
        store.append_turn(f"k{i}", "q", "a")
    assert store.active_sessions() == 5

    clock.advance(11)
    assert store.active_sessions() == 0


def test_cleanup_only_removes_expired_ones():
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=100, clock=clock)
    store.append_turn("old", "q", "a")
    clock.advance(60)
    store.append_turn("new", "q", "a")
    clock.advance(60)  # old 已 120 秒过期，new 才 60 秒
    assert store.get_history("old") == []
    assert store.get_history("new") != []
    assert store.active_sessions() == 1


# --------------------------------------------------------------------------
# clear
# --------------------------------------------------------------------------


def test_clear_removes_only_target_session():
    store = InMemorySessionStore()
    store.append_turn("k1", "q", "a")
    store.append_turn("k2", "q", "a")
    store.clear("k1")
    assert store.get_history("k1") == []
    assert store.get_history("k2") != []


def test_clear_unknown_key_is_safe():
    InMemorySessionStore().clear("never-existed")


def test_can_append_again_after_clear():
    store = InMemorySessionStore()
    store.append_turn("k", "q1", "a1")
    store.clear("k")
    store.append_turn("k", "q2", "a2")
    assert store.get_history("k") == [
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]


# --------------------------------------------------------------------------
# 从配置构造
# --------------------------------------------------------------------------


def test_from_config():
    cfg = SessionConfig(scope="group", max_turns=3, ttl_seconds=60)
    store = SessionStore.from_config(cfg)
    assert isinstance(store, InMemorySessionStore)
    assert isinstance(store, SessionStore)
    assert store.scope == "group"
    assert store.max_turns == 3
    assert store.ttl_seconds == 60


def test_from_config_scope_drives_key():
    store = SessionStore.from_config(SessionConfig(scope="user"))
    a = frame("L220104", chatid="c1", chattype="group")
    b = frame("L220104", chatid="c2", chattype="group")
    assert store.key_for(a) == store.key_for(b)


@pytest.mark.parametrize("scope", ["group_user", "group", "user"])
def test_end_to_end_isolation_via_store(scope):
    """走完整路径：帧 → key → 存 → 读，确认 scope 真的影响到了数据隔离"""
    store = InMemorySessionStore(scope=scope)
    a = frame("L220104", chatid="chat-1", chattype="group")
    b = frame("L220105", chatid="chat-1", chattype="group")

    store.append_turn(store.key_for(a), "我是A", "回A")
    b_history = store.get_history(store.key_for(b))

    if scope == "group":
        assert b_history[0]["content"] == "我是A"  # 整群共享
    else:
        assert b_history == []  # 按人隔离


# --------------------------------------------------------------------------
# SessionStore 抽象接口（门户防返工：为 Redis 实现留口子）
# --------------------------------------------------------------------------


def test_sessionstore_is_abstract_and_cannot_be_instantiated():
    """SessionStore 是接口，不能直接实例化（具体后端用 InMemory/Redis 实现）"""
    with pytest.raises(TypeError):
        SessionStore()  # type: ignore[abstract]


def test_inmemory_implements_the_interface():
    """InMemorySessionStore 是 SessionStore 的具体实现"""
    store = InMemorySessionStore()
    assert isinstance(store, SessionStore)


def test_from_config_passes_clock_through():
    """from_config 的 clock 注入要透传到内存实现，TTL 测试才可控"""
    clock = FakeClock()
    store = SessionStore.from_config(
        SessionConfig(ttl_seconds=100), clock=clock
    )
    store.append_turn("k", "q", "a")
    assert store.get_history("k") != []
    clock.advance(101)
    assert store.get_history("k") == []

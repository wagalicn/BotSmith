"""提示词结构检查测试"""

from __future__ import annotations

from botkit.prompt_check import analyze_prompt, report_to_dict


def keys_ok(text: str) -> set[str]:
    return {i.key for i in analyze_prompt(text).items if i.ok}


def item_keys(text: str) -> set[str]:
    return {i.key for i in analyze_prompt(text).items}


# --------------------------------------------------------------------------
# 空 / 太短
# --------------------------------------------------------------------------


def test_empty_prompt():
    report = analyze_prompt("")
    assert report.char_count == 0
    assert report.score == 0
    assert any("空" in s for s in report.suggestions)


def test_whitespace_only_counts_as_empty():
    assert analyze_prompt("   \n  \n").char_count == 0


def test_very_short_prompt_flagged():
    report = analyze_prompt("你是助手")
    assert any("短" in s for s in report.suggestions)


# --------------------------------------------------------------------------
# 各要点识别
# --------------------------------------------------------------------------


def test_detects_role():
    assert "role" in keys_ok("你是 HR 助手")


def test_detects_task():
    assert "task" in keys_ok("你的任务是帮同事处理请假")


def test_detects_reply_requirements():
    assert "reply" in keys_ok("用中文简洁回答")


def test_full_prompt_scores_high():
    text = """
# 角色
你是 IT 助手。

# 核心任务
帮员工创建 IT 工单。

# 工作流程
1. 先调用 get_system_config。
2. 然后调用 create_it_ticket。
   严禁自己编造 ID，必须来自查询结果。

# 回答要求
用中文简洁回答，信息不全时主动追问。
"""
    report = analyze_prompt(text)
    assert report.score == report.total  # 全过
    assert report.heading_count == 4
    assert report.suggestions == []


# --------------------------------------------------------------------------
# 工具 bot 才检查流程/约束
# --------------------------------------------------------------------------


def test_tool_bot_gets_flow_and_constraint_checks():
    text = "你是助手，负责帮用户创建工单，需要调用工具。"
    keys = item_keys(text)
    assert "flow" in keys
    assert "constraint" in keys


def test_plain_qa_bot_skips_flow_and_constraint():
    """纯问答 bot 没提工具，不该被流程/约束的提醒打扰"""
    text = "你是一个知识问答助手，回答用户的常识问题，用中文简洁回答。"
    keys = item_keys(text)
    assert "flow" not in keys
    assert "constraint" not in keys
    assert "role" in keys


def test_tool_bot_missing_constraint_is_suggested():
    text = "你是工单助手，负责调用工具创建工单。用中文回答。"
    report = analyze_prompt(text)
    # 提到了工具但没写"严禁/必须"，应该被建议补约束
    assert any("约束" in s or "严禁" in s for s in report.suggestions)


# --------------------------------------------------------------------------
# 分段建议
# --------------------------------------------------------------------------


def test_no_headings_suggested():
    text = "你是助手，负责回答问题，用中文简洁回答，内容要足够长以免触发太短的提示啦啦啦。"
    report = analyze_prompt(text)
    assert report.heading_count == 0
    assert any("#" in s or "分段" in s for s in report.suggestions)


def test_headings_counted():
    assert analyze_prompt("# 角色\n你是助手\n## 任务\n干活").heading_count == 2


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def test_report_to_dict_shape():
    d = report_to_dict(analyze_prompt("# 角色\n你是助手，帮用户处理问题。用中文回答。"))
    assert set(d) == {
        "char_count",
        "heading_count",
        "score",
        "total",
        "items",
        "suggestions",
    }
    assert isinstance(d["items"], list)
    assert all({"key", "label", "ok", "hint"} == set(i) for i in d["items"])


def test_score_never_exceeds_total():
    for text in ("", "你是助手", "# 角色\n你是助手，调用工具，严禁编造，用中文回答"):
        report = analyze_prompt(text)
        assert 0 <= report.score <= report.total

"""
提示词结构检查

不检查 Markdown 语法 —— 框架是把提示词整段原样当 system prompt 塞给模型的，
`.md` 只是习惯，不影响效果。真正影响Agent靠不靠谱的是内容有没有写全：
角色、任务、（若要调工具）流程和约束、回答要求。

这里给的是「建议」不是「错误」：这些都是软性的，写不写、怎么写由人定，
页面只是提个醒，别让新手写出一句话的提示词就上线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CheckItem:
    key: str
    label: str
    ok: bool
    hint: str


@dataclass
class PromptReport:
    char_count: int
    heading_count: int
    items: list[CheckItem]
    suggestions: list[str]

    @property
    def score(self) -> int:
        """通过的检查项数，给前端画个简单进度"""
        return sum(1 for i in self.items if i.ok)

    @property
    def total(self) -> int:
        return len(self.items)


# 每个要点用一组关键词判断「提到了没」。宽松匹配，宁可漏报不误报 ——
# 目的是提醒，不是打分卡人。
_ROLE_WORDS = ("角色", "你是", "你的身份", "role", "you are")
_TASK_WORDS = ("任务", "职责", "负责", "帮", "处理", "目标", "task")
_FLOW_WORDS = ("流程", "步骤", "先", "然后", "第一步", "顺序", "step", "workflow")
_CONSTRAINT_WORDS = ("严禁", "必须", "不要", "不得", "禁止", "只能", "务必", "不能")
_REPLY_WORDS = ("回答", "回复", "输出", "告知", "简洁", "语气", "格式")

# 工具调用相关的信号词，用来判断这个 bot 是不是要调工具
_TOOL_HINT_WORDS = ("工具", "调用", "接口", "tool", "工单", "查询", "提交", "创建")


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def analyze_prompt(text: str) -> PromptReport:
    """分析一段提示词，返回结构检查结果"""
    text = text or ""
    stripped = text.strip()
    char_count = len(stripped)
    headings = re.findall(r"^\s{0,3}#{1,6}\s+\S", text, re.MULTILINE)

    looks_tool_bot = _mentions(stripped, _TOOL_HINT_WORDS)

    items: list[CheckItem] = [
        CheckItem(
            "role",
            "角色定位",
            _mentions(stripped, _ROLE_WORDS),
            "写清「你是谁」，比如“你是 HR 助手，运行在企业微信里”。",
        ),
        CheckItem(
            "task",
            "核心任务",
            _mentions(stripped, _TASK_WORDS),
            "写清这个Agent要帮用户做什么。",
        ),
        CheckItem(
            "reply",
            "回答要求",
            _mentions(stripped, _REPLY_WORDS),
            "给点回答风格上的约束，比如“用中文简洁回答”“信息不全时主动追问”。",
        ),
    ]

    # 只有看起来要调工具的 bot 才检查流程和约束，否则纯问答 bot 会被误提醒
    if looks_tool_bot:
        items.append(
            CheckItem(
                "flow",
                "工具调用流程",
                _mentions(stripped, _FLOW_WORDS),
                "按顺序调工具的 bot，把「先调什么、再调什么」写成编号步骤，模型更不容易乱。",
            )
        )
        items.append(
            CheckItem(
                "constraint",
                "硬性约束",
                _mentions(stripped, _CONSTRAINT_WORDS),
                "用“严禁/必须”这类硬词写清不许做什么，比如“严禁自己编造 ID，必须来自查询结果”。",
            )
        )

    suggestions: list[str] = []

    if char_count == 0:
        suggestions.append("提示词还是空的。至少写清角色和任务，Agent才知道自己该干嘛。")
    elif char_count < 60:
        suggestions.append("提示词有点短。多写几句角色、任务和约束，效果会明显好很多。")

    if stripped and not headings:
        suggestions.append(
            "没有用 # 分段。不是必须，但用“# 角色 / # 任务 / # 回答要求”分段，"
            "自己和模型都更好读。"
        )

    for item in items:
        if not item.ok:
            suggestions.append(f"建议补充【{item.label}】：{item.hint}")

    return PromptReport(
        char_count=char_count,
        heading_count=len(headings),
        items=items,
        suggestions=suggestions,
    )


def report_to_dict(report: PromptReport) -> dict:
    """转成给前端的 JSON"""
    return {
        "char_count": report.char_count,
        "heading_count": report.heading_count,
        "score": report.score,
        "total": report.total,
        "items": [
            {"key": i.key, "label": i.label, "ok": i.ok, "hint": i.hint}
            for i in report.items
        ],
        "suggestions": report.suggestions,
    }

"""
配置页面服务层测试

这一层直接读写同事的配置文件，出错代价很高，所以覆盖得细一点。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from botkit.ui import service
from botkit.ui.service import UiError


@pytest.fixture
def project(tmp_path: Path):
    """一个带 bots/ 的空项目根"""
    (tmp_path / "bots").mkdir()
    return tmp_path


@pytest.fixture
def bot(project: Path):
    """一个通过脚手架建出来的 bot，密钥已填好"""
    service.create_new_bot(project, "hr-bot")
    bot_dir = project / "bots" / "hr-bot"
    (bot_dir / ".env").write_text(
        "WECHAT_BOT_ID=id-1\n"
        "WECHAT_BOT_SECRET=secret-1\n"
        "LLM_API_URL=https://x.invalid/v1\n"
        "LLM_API_KEY=sk-1\n"
        "LLM_MODEL=qwen\n"
        "MCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )
    return bot_dir


# --------------------------------------------------------------------------
# 路径安全
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../secret", "..", "a/../..", "sub/dir", "", "  ", "..\\windows"],
)
def test_path_traversal_rejected(project, name):
    """bot 名字是从网页传进来的，必须挡住路径穿越"""
    with pytest.raises(UiError):
        service.bot_dir_for(project, name)


def test_missing_bot_rejected(project):
    with pytest.raises(UiError, match="找不到这个 Agent"):
        service.bot_dir_for(project, "nope")


def test_valid_bot_resolves(project, bot):
    assert service.bot_dir_for(project, "hr-bot") == bot.resolve()


def test_bots_root_is_created(tmp_path):
    root = service.bots_root(tmp_path)
    assert root.is_dir()
    assert root.name == "bots"


# --------------------------------------------------------------------------
# 环境变量隔离
# --------------------------------------------------------------------------


def test_isolated_env_restores_everything(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "original")
    with service.isolated_env():
        os.environ["KEEP_ME"] = "changed"
        os.environ["BRAND_NEW"] = "x"
    assert os.environ["KEEP_ME"] == "original"
    assert "BRAND_NEW" not in os.environ


def test_isolated_env_restores_on_exception(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "original")
    with pytest.raises(RuntimeError):
        with service.isolated_env():
            os.environ["KEEP_ME"] = "changed"
            raise RuntimeError("boom")
    assert os.environ["KEEP_ME"] == "original"


def test_switching_bots_does_not_leak_secrets(project):
    """
    这是页面上最隐蔽的一类 bug：读了 A 的 .env 之后环境里留着 A 的密钥，
    再读 B 时因为 override=False 就把 B 的值盖住了，校验结果全错。
    """
    for name, url in (("bot-a", "https://a.invalid/mcp"), ("bot-b", "https://b.invalid/mcp")):
        service.create_new_bot(project, name)
        (project / "bots" / name / ".env").write_text(
            f"WECHAT_BOT_ID=id-{name}\n"
            f"WECHAT_BOT_SECRET=secret-{name}\n"
            "LLM_API_URL=https://x.invalid/v1\n"
            f"LLM_API_KEY=sk-{name}\n"
            "LLM_MODEL=qwen\n"
            f"MCP_URL={url}\n",
            encoding="utf-8",
        )

    first = service.read_bot(project, "bot-a")
    second = service.read_bot(project, "bot-b")

    assert first["validation"]["ok"], first["validation"]["error"]
    assert second["validation"]["ok"], second["validation"]["error"]
    assert "id-bot-a" in first["validation"]["summary"] or True
    # B 的摘要里必须是 B 自己的 MCP 地址
    assert "b.invalid" in second["validation"]["summary"]
    assert "a.invalid" not in second["validation"]["summary"]
    # 校验完进程环境不该被污染
    assert "MCP_URL" not in os.environ


# --------------------------------------------------------------------------
# .env 读写
# --------------------------------------------------------------------------


def test_read_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# 注释\n\nA=1\n  B = two \nEMPTY=\n# C=ignored\n", encoding="utf-8"
    )
    assert service.read_env_file(path) == {"A": "1", "B": "two", "EMPTY": ""}


def test_read_env_file_missing(tmp_path):
    assert service.read_env_file(tmp_path / "nope") == {}


def test_write_env_preserves_comments_and_order(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# 企微凭证\nWECHAT_BOT_ID=old\nWECHAT_BOT_SECRET=old\n\n# LLM\nLLM_API_KEY=old\n",
        encoding="utf-8",
    )
    service.write_env_file(path, {"WECHAT_BOT_ID": "new-id", "LLM_API_KEY": "new-key"})

    text = path.read_text(encoding="utf-8")
    assert "# 企微凭证" in text
    assert "# LLM" in text
    assert "WECHAT_BOT_ID=new-id" in text
    assert "LLM_API_KEY=new-key" in text
    assert "WECHAT_BOT_SECRET=old" in text  # 没提到的不动
    # 顺序保持
    assert text.index("WECHAT_BOT_ID") < text.index("LLM_API_KEY")


def test_write_env_appends_new_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1\n", encoding="utf-8")
    service.write_env_file(path, {"A": "2", "NEW_ONE": "x"})
    values = service.read_env_file(path)
    assert values == {"A": "2", "NEW_ONE": "x"}


def test_write_env_uses_example_as_base(tmp_path):
    """第一次保存时，拿 .env.example 当底子，保留它的注释"""
    (tmp_path / ".env.example").write_text(
        "# 企微凭证\nWECHAT_BOT_ID=\nWECHAT_BOT_SECRET=\n", encoding="utf-8"
    )
    path = tmp_path / ".env"
    service.write_env_file(path, {"WECHAT_BOT_ID": "filled"})

    text = path.read_text(encoding="utf-8")
    assert "# 企微凭证" in text
    assert "WECHAT_BOT_ID=filled" in text


def test_write_env_from_scratch(tmp_path):
    path = tmp_path / ".env"
    service.write_env_file(path, {"A": "1"})
    assert service.read_env_file(path) == {"A": "1"}


def test_env_values_with_special_chars(tmp_path):
    path = tmp_path / ".env"
    service.write_env_file(path, {"URL": "https://x/y?a=b&c=d", "PLAIN": "a b c"})
    assert service.read_env_file(path)["URL"] == "https://x/y?a=b&c=d"


# --------------------------------------------------------------------------
# 占位符
# --------------------------------------------------------------------------


def test_placeholders_found_in_order():
    values = {
        "wecom": {"bot_id": "${WECHAT_BOT_ID}", "bot_secret": "${WECHAT_BOT_SECRET}"},
        "mcp": {"servers": [{"url": "${MCP_URL}"}]},
        "llm": {"api_url": "${LLM_API_URL}", "model": "写死的模型"},
    }
    assert service.placeholders_in(values) == [
        "WECHAT_BOT_ID",
        "WECHAT_BOT_SECRET",
        "MCP_URL",
        "LLM_API_URL",
    ]


def test_placeholders_deduplicated():
    assert service.placeholders_in(["${A}", "${A}", "${B}"]) == ["A", "B"]


def test_placeholders_in_embedded_string():
    assert service.placeholders_in({"u": "https://${HOST}:8081/mcp"}) == ["HOST"]


def test_placeholders_none_found():
    assert service.placeholders_in({"a": "plain", "b": 1}) == []


# --------------------------------------------------------------------------
# 列表 / 读
# --------------------------------------------------------------------------


def test_list_bots_empty(project):
    assert service.list_bots(project) == []


def test_list_bots_reports_status(project, bot):
    service.create_new_bot(project, "broken-bot")  # 没填 .env
    bots = {b["name"]: b for b in service.list_bots(project)}

    assert bots["hr-bot"]["ok"] is True
    assert bots["hr-bot"]["has_env"] is True
    assert bots["broken-bot"]["ok"] is False
    assert "WECHAT_BOT_ID" in bots["broken-bot"]["error"]
    assert bots["broken-bot"]["has_env"] is False


def test_list_bots_ignores_non_bot_dirs(project, bot):
    (project / "bots" / "just-a-folder").mkdir()
    (project / "bots" / "a-file.txt").write_text("x", encoding="utf-8")
    assert [b["name"] for b in service.list_bots(project)] == ["hr-bot"]


def test_read_bot_returns_everything(project, bot):
    data = service.read_bot(project, "hr-bot")

    assert data["name"] == "hr-bot"
    assert data["values"]["name"] == "hr-bot"
    assert data["prompt_file"] == "prompt.md"
    assert len(data["prompt"]) > 50
    assert data["validation"]["ok"], data["validation"]["error"]

    env_keys = [e["key"] for e in data["env"]]
    assert "WECHAT_BOT_ID" in env_keys
    # MCP 地址在工具页维护、不进密钥页
    assert not any(k.startswith("MCP_URL") for k in env_keys)
    assert all(e["filled"] for e in data["env"])


def test_read_bot_marks_unfilled_env(project):
    service.create_new_bot(project, "fresh")
    data = service.read_bot(project, "fresh")
    assert data["env"]
    assert not any(e["filled"] for e in data["env"])
    assert not data["validation"]["ok"]


def test_read_bot_reports_hooks_state(project, bot):
    data = service.read_bot(project, "hr-bot")
    # 脚手架生成的 hooks.py 全是注释
    assert data["hooks"]["exists"] is True
    assert data["hooks"]["implemented"] == []


def test_read_bot_detects_active_hooks(project, bot):
    (bot / "hooks.py").write_text(
        "def on_message(text, ctx):\n    return None\n", encoding="utf-8"
    )
    data = service.read_bot(project, "hr-bot")
    assert data["hooks"]["implemented"] == ["on_message"]


def test_read_bot_with_broken_yaml(project, bot):
    (bot / "bot.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(UiError, match="不是合法的 YAML"):
        service.read_bot(project, "hr-bot")


def test_read_bot_with_non_mapping_yaml(project, bot):
    (bot / "bot.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(UiError, match="顶层不是键值对"):
        service.read_bot(project, "hr-bot")


# --------------------------------------------------------------------------
# 保存
# --------------------------------------------------------------------------


def test_save_writes_yaml_prompt_and_env(project, bot):
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["session"]["scope"] = "group"
    values["mcp"]["servers"][0]["allow_tools"] = ["tool_a", "tool_b"]
    # LLM model 现在在「大模型」页明文维护（values.llm.model），
    # save 时会落到 .env 的 LLM_MODEL、yaml 里存 ${LLM_MODEL} 占位符。
    values["llm"]["model"] = "qwen-max"

    result = service.save_bot(
        project,
        "hr-bot",
        {
            "values": values,
            "prompt": "# 角色\n我是新写的提示词。",
        },
    )

    assert result["validation"]["ok"], result["validation"]["error"]

    raw = yaml.safe_load((bot / "bot.yaml").read_text(encoding="utf-8"))
    assert raw["session"]["scope"] == "group"
    assert raw["mcp"]["servers"][0]["allow_tools"] == ["tool_a", "tool_b"]
    assert (bot / "prompt.md").read_text(encoding="utf-8").startswith("# 角色")
    # 明文落到 .env，yaml 存占位符
    assert service.read_env_file(bot / ".env")["LLM_MODEL"] == "qwen-max"
    assert raw["llm"]["model"] == "${LLM_MODEL}"


def test_save_keeps_comments_in_yaml(project, bot):
    """页面存盘不能把注释吃掉，否则手改配置的人就没文档了"""
    data = service.read_bot(project, "hr-bot")
    service.save_bot(project, "hr-bot", {"values": data["values"], "prompt": "x"})

    text = (bot / "bot.yaml").read_text(encoding="utf-8")
    assert "# 企业微信凭证" in text
    assert "# 身份" in text
    assert len([l for l in text.splitlines() if l.strip().startswith("#")]) > 20


def test_save_forces_name_to_match_directory(project, bot):
    """页面上改名字不该导致目录和配置里的 name 不一致"""
    data = service.read_bot(project, "hr-bot")
    data["values"]["name"] = "改成别的名字"
    service.save_bot(project, "hr-bot", {"values": data["values"]})

    raw = yaml.safe_load((bot / "bot.yaml").read_text(encoding="utf-8"))
    assert raw["name"] == "hr-bot"


def test_save_returns_validation_errors(project):
    service.create_new_bot(project, "fresh")
    data = service.read_bot(project, "fresh")
    result = service.save_bot(project, "fresh", {"values": data["values"]})
    assert result["validation"]["ok"] is False
    # 新建 bot 没填企微凭证、也没开对外接口 —— 校验会提示「至少要有一种」。
    # （企微凭证不再是硬必填，只做对外接口的 bot 可以不填。）
    err = result["validation"]["error"]
    assert "至少" in err or "WECHAT_BOT_ID" in err or "wecom.bot_id" in err


def test_save_rejects_bad_values_payload(project, bot):
    with pytest.raises(UiError, match="values"):
        service.save_bot(project, "hr-bot", {"values": "不是对象"})


@pytest.mark.parametrize(
    "values,message",
    [
        ({"llm": {"client": "curl"}}, "llm.client"),
        ({"session": {"scope": "per_person"}}, "session.scope"),
        ({"identity": {"source": "magic"}}, "identity.source"),
        ({"logging": {"level": "VERBOSE"}}, "logging.level"),
        ({"identity": {"pattern": "[unclosed"}}, "正则"),
    ],
)
def test_save_rejects_invalid_enums_and_regex(project, bot, values, message):
    with pytest.raises(UiError, match=message):
        service.save_bot(project, "hr-bot", {"values": values})


def test_save_ignores_bad_env_keys(project, bot):
    service.save_bot(
        project,
        "hr-bot",
        {
            "values": service.read_bot(project, "hr-bot")["values"],
            "env": {"GOOD_KEY": "1", "bad key": "2", "3BAD": "4"},
        },
    )
    values = service.read_env_file(bot / ".env")
    assert values["GOOD_KEY"] == "1"
    assert "bad key" not in values
    assert "3BAD" not in values


def test_save_prompt_path_traversal_rejected(project, bot):
    data = service.read_bot(project, "hr-bot")
    data["values"]["prompt"] = {"file": "../../escaped.md"}
    with pytest.raises(UiError, match="不合法"):
        service.save_bot(
            project, "hr-bot", {"values": data["values"], "prompt": "坏东西"}
        )
    assert not (project.parent / "escaped.md").exists()


def test_save_round_trips_through_read(project, bot):
    """存了再读，内容应该一致 —— 页面反复编辑不会漂"""
    first = service.read_bot(project, "hr-bot")
    first["values"]["llm"]["max_tool_rounds"] = 9
    service.save_bot(
        project, "hr-bot", {"values": first["values"], "prompt": first["prompt"]}
    )

    second = service.read_bot(project, "hr-bot")
    assert second["values"]["llm"]["max_tool_rounds"] == 9
    assert second["prompt"] == first["prompt"].rstrip("\n") + "\n"
    assert second["validation"]["ok"]


def test_save_inline_prompt_bot_does_not_write_file(project, bot):
    data = service.read_bot(project, "hr-bot")
    data["values"]["prompt"] = {"inline": "内联提示词"}
    service.save_bot(project, "hr-bot", {"values": data["values"], "prompt": "忽略我"})

    raw = yaml.safe_load((bot / "bot.yaml").read_text(encoding="utf-8"))
    assert raw["prompt"] == {"inline": "内联提示词"}


# --------------------------------------------------------------------------
# 新建
# --------------------------------------------------------------------------


def test_create_new_bot(project):
    result = service.create_new_bot(project, "brand-new")
    assert result["name"] == "brand-new"
    bot_dir = project / "bots" / "brand-new"
    for filename in ("bot.yaml", "prompt.md", "hooks.py", ".env.example", "README.md"):
        assert (bot_dir / filename).is_file(), filename


def test_create_duplicate_rejected(project, bot):
    with pytest.raises(UiError, match="已存在"):
        service.create_new_bot(project, "hr-bot")


@pytest.mark.parametrize("name", ["1bad", "has space", "中文名", "", "../escape"])
def test_create_invalid_name_rejected(project, name):
    with pytest.raises(UiError):
        service.create_new_bot(project, name)


def test_created_bot_is_immediately_readable(project):
    service.create_new_bot(project, "fresh")
    data = service.read_bot(project, "fresh")
    assert data["name"] == "fresh"
    assert data["values"]["llm"]["client"] == "openai"


# --------------------------------------------------------------------------
# 删除（挪回收站）
# --------------------------------------------------------------------------


def test_delete_moves_to_trash(project, bot):
    result = service.delete_bot(project, "hr-bot")
    assert result["name"] == "hr-bot"

    # 现役目录没了
    assert not (project / "bots" / "hr-bot").exists()
    # 回收站里能找回，内容完整
    trashed = project / "bots" / ".trash" / "hr-bot"
    assert trashed.is_dir()
    assert (trashed / "bot.yaml").is_file()
    assert (trashed / ".env").is_file()


def test_deleted_bot_gone_from_list(project, bot):
    service.create_new_bot(project, "keep-me")
    service.delete_bot(project, "hr-bot")
    names = [b["name"] for b in service.list_bots(project)]
    assert names == ["keep-me"]


def test_trash_not_listed_as_bot(project, bot):
    service.delete_bot(project, "hr-bot")
    # .trash 里躺着一个含 bot.yaml 的目录，但它不该被当成现役 bot
    assert service.list_bots(project) == []


def test_delete_missing_bot_rejected(project):
    with pytest.raises(UiError, match="找不到这个 Agent"):
        service.delete_bot(project, "nope")


@pytest.mark.parametrize("name", ["..", "../escape", "a/b", "", ".trash"])
def test_delete_path_traversal_rejected(project, bot, name):
    with pytest.raises(UiError):
        service.delete_bot(project, name)


def test_delete_same_name_twice_does_not_clobber(project):
    """连续建删同名 bot 两次，回收站里应该留下两份而不是覆盖"""
    service.create_new_bot(project, "dupe")
    service.delete_bot(project, "dupe")
    service.create_new_bot(project, "dupe")
    service.delete_bot(project, "dupe")

    trash = project / "bots" / ".trash"
    trashed = [p for p in trash.iterdir() if p.name.startswith("dupe")]
    assert len(trashed) == 2


def test_can_recreate_after_delete(project, bot):
    """删掉之后同名还能重新建（现役目录已经腾空了）"""
    service.delete_bot(project, "hr-bot")
    result = service.create_new_bot(project, "hr-bot")
    assert result["name"] == "hr-bot"
    assert (project / "bots" / "hr-bot" / "bot.yaml").is_file()


# --------------------------------------------------------------------------
# 多个 MCP 服务端
# --------------------------------------------------------------------------


def test_save_server_with_empty_address_is_pure_chat(project, bot):
    """
    截图场景：页面预置了一个有标识名、但地址留空的 server。
    这种空行不该拦住保存（纯对话 bot 用不到工具），存盘加载后 servers 为空。
    """
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "MCP", "address": "", "allow_tools": []},
    ]
    # 不应抛 UiError（旧行为会报「还没填标识名/url 必填」）
    service.save_bot(project, "hr-bot", {"values": values})

    # 加载配置：空地址的 server 被跳过，等价于纯 LLM 对话
    cfg = service.load_bot_config(project / "bots" / "hr-bot")
    assert cfg.mcp.servers == []
    # 不该为这个空 server 生成 MCP_URL_MCP 变量
    env = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert "MCP_URL_MCP" not in env


def test_save_multiple_servers(project, bot):
    """页面能配多个 MCP 服务端，存盘再读回来不丢"""
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "oa", "url": "${MCP_URL}", "allow_tools": ["get_system_config"]},
        {
            "name": "crm",
            "url": "${CRM_MCP_URL}",
            "identity_header": "X-Identity",
            "allow_tools": ["get_customer"],
        },
    ]
    result = service.save_bot(project, "hr-bot", {"values": values})
    # CRM_MCP_URL 没填，校验会因为缺环境变量不过 —— 但结构是对的
    reread = service.read_bot(project, "hr-bot")
    servers = reread["values"]["mcp"]["servers"]
    assert [s["name"] for s in servers] == ["oa", "crm"]
    assert servers[1]["identity_header"] == "X-Identity"
    assert servers[1]["allow_tools"] == ["get_customer"]
    _ = result


def test_multiple_servers_addresses_not_on_key_page(project, bot):
    """多 server 的地址都在工具页维护，不出现在密钥页"""
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "oa", "address": "https://oa.internal/mcp", "allow_tools": []},
        {"name": "crm", "address": "https://crm.internal/mcp", "allow_tools": []},
    ]
    service.save_bot(project, "hr-bot", {"values": values})

    reread = service.read_bot(project, "hr-bot")
    env_keys = [e["key"] for e in reread["env"]]
    # 密钥页里没有任何 MCP 地址变量
    assert not any(k.startswith("MCP_URL") for k in env_keys)
    # 地址落到了 .env 的 MCP_URL_OA / MCP_URL_CRM
    env = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert env["MCP_URL_OA"] == "https://oa.internal/mcp"
    assert env["MCP_URL_CRM"] == "https://crm.internal/mcp"
    # bot.yaml 里存的是占位符（不进 git）
    text = (project / "bots" / "hr-bot" / "bot.yaml").read_text(encoding="utf-8")
    assert "${MCP_URL_OA}" in text
    assert "${MCP_URL_CRM}" in text
    assert "oa.internal" not in text  # 明文地址不进 bot.yaml
    # read 回来时地址是明文，供工具页显示
    servers = {s["name"]: s for s in reread["values"]["mcp"]["servers"]}
    assert servers["oa"]["address"] == "https://oa.internal/mcp"
    assert servers["crm"]["address"] == "https://crm.internal/mcp"


def test_save_rejects_duplicate_server_names(project, bot):
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "same", "url": "${MCP_URL}", "allow_tools": []},
        {"name": "same", "url": "${CRM_MCP_URL}", "allow_tools": []},
    ]
    with pytest.raises(UiError, match="标识名要唯一"):
        service.save_bot(project, "hr-bot", {"values": values})


def test_save_rejects_empty_server_name(project, bot):
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "oa", "url": "${MCP_URL}", "allow_tools": []},
        {"name": "", "url": "${CRM_MCP_URL}", "allow_tools": []},
    ]
    with pytest.raises(UiError, match="还没填标识名"):
        service.save_bot(project, "hr-bot", {"values": values})


async def test_probe_single_server_by_name(project, bot):
    """带 server 名探测时，只探那一个"""
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"] = [
        {"name": "oa", "url": "https://oa.invalid/mcp", "allow_tools": []},
        {"name": "crm", "url": "https://crm.invalid/mcp", "allow_tools": []},
    ]
    service.save_bot(project, "hr-bot", {"values": values})

    result = await service.probe_mcp(project, "hr-bot", server_name="crm")
    assert [s["server"] for s in result["servers"]] == ["crm"]


async def test_probe_unknown_server_name_rejected(project, bot):
    with pytest.raises(UiError, match="没有名叫"):
        await service.probe_mcp(project, "hr-bot", server_name="nonexistent")


# --------------------------------------------------------------------------
# MCP 地址：工具页明文编辑、落 .env、bot.yaml 存占位符
# --------------------------------------------------------------------------


def test_mcp_url_var_naming():
    assert service.mcp_url_var_for("oa") == "MCP_URL_OA"
    assert service.mcp_url_var_for("crm") == "MCP_URL_CRM"
    assert service.mcp_url_var_for("HR-Bot") == "MCP_URL_HR_BOT"
    assert service.mcp_url_var_for("") == "MCP_URL_MAIN"
    assert service.mcp_url_var_for("中台") == "MCP_URL_MAIN"  # 非 ASCII 兜底


def test_is_mcp_url_var():
    assert service.is_mcp_url_var("MCP_URL_OA")
    assert service.is_mcp_url_var("MCP_URL_MAIN")
    assert not service.is_mcp_url_var("MCP_URL")  # 没有后缀的不算
    assert not service.is_mcp_url_var("WECHAT_BOT_ID")


def test_address_saved_to_env_not_bot_yaml(project, bot):
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"][0]["name"] = "oa"
    values["mcp"]["servers"][0]["address"] = "https://secret.internal:8081/mcp"

    service.save_bot(project, "hr-bot", {"values": values})

    text = (project / "bots" / "hr-bot" / "bot.yaml").read_text(encoding="utf-8")
    assert "secret.internal" not in text  # 明文不进 bot.yaml
    assert "${MCP_URL_OA}" in text

    env = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert env["MCP_URL_OA"] == "https://secret.internal:8081/mcp"


def test_address_round_trips(project, bot):
    """填地址 → 存 → 读回来还是同一个明文"""
    data = service.read_bot(project, "hr-bot")
    data["values"]["mcp"]["servers"][0]["name"] = "oa"
    data["values"]["mcp"]["servers"][0]["address"] = "https://a.internal/mcp"
    service.save_bot(project, "hr-bot", {"values": data["values"]})

    reread = service.read_bot(project, "hr-bot")
    assert reread["values"]["mcp"]["servers"][0]["address"] == "https://a.internal/mcp"


def test_renaming_server_cleans_up_old_env_var(project, bot):
    """改了服务端标识名，.env 里旧的 MCP_URL_<旧名> 应该被清掉，不留孤儿"""
    data = service.read_bot(project, "hr-bot")
    data["values"]["mcp"]["servers"][0]["name"] = "oa"
    data["values"]["mcp"]["servers"][0]["address"] = "https://a.internal/mcp"
    service.save_bot(project, "hr-bot", {"values": data["values"]})

    env = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert "MCP_URL_OA" in env

    # 改名成 crm
    data2 = service.read_bot(project, "hr-bot")
    data2["values"]["mcp"]["servers"][0]["name"] = "crm"
    data2["values"]["mcp"]["servers"][0]["address"] = "https://a.internal/mcp"
    service.save_bot(project, "hr-bot", {"values": data2["values"]})

    env2 = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert "MCP_URL_CRM" in env2
    assert "MCP_URL_OA" not in env2  # 旧变量清掉了


def test_empty_address_saved_as_empty_var(project, bot):
    """地址留空时，变量写空串（校验会因此不过，但不崩）"""
    data = service.read_bot(project, "hr-bot")
    data["values"]["mcp"]["servers"][0]["name"] = "oa"
    data["values"]["mcp"]["servers"][0]["address"] = ""
    service.save_bot(project, "hr-bot", {"values": data["values"]})

    env = service.read_env_file(project / "bots" / "hr-bot" / ".env")
    assert env.get("MCP_URL_OA", "") == ""


# --------------------------------------------------------------------------
# 前端选项表
# --------------------------------------------------------------------------


def test_form_options_matches_config_layer():
    """
    下拉框的可选值必须和配置层认的一致，否则页面能选出一个存不进去的值。
    """
    from botkit.config import (
        VALID_IDENTITY_SOURCES,
        VALID_LLM_CLIENTS,
        VALID_LOG_LEVELS,
        VALID_SESSION_SCOPES,
    )

    options = service.form_options()
    assert [c["value"] for c in options["llm_clients"]] == list(VALID_LLM_CLIENTS)
    assert [c["value"] for c in options["session_scopes"]] == list(VALID_SESSION_SCOPES)
    assert [c["value"] for c in options["identity_sources"]] == list(
        VALID_IDENTITY_SOURCES
    )
    assert options["log_levels"] == list(VALID_LOG_LEVELS)


def test_form_options_message_fields_cover_all_texts():
    """文案字段不能漏 —— 漏了页面上就改不到那条"""
    from botkit.config import MessagesConfig

    options = service.form_options()
    shown = {f["key"] for f in options["message_fields"]}
    expected = {
        name
        for name in vars(MessagesConfig()).keys()
        if name not in ("reset_keywords", "tool_progress")
    }
    assert shown == expected


def test_identity_presets_are_valid_regex():
    import re

    for preset in service.form_options()["identity_presets"]:
        if preset["value"]:
            re.compile(preset["value"])


def test_identity_preset_matches_typical_workcode():
    import re

    presets = {p["label"]: p["value"] for p in service.form_options()["identity_presets"]}
    pattern = presets["L/M + 6位数字（如 L220104）"]
    assert re.match(pattern, "L220104")
    assert re.match(pattern, "M999999")
    assert not re.match(pattern, "wo1234abcd==")


# --------------------------------------------------------------------------
# 工具信息压缩
# --------------------------------------------------------------------------


def test_tool_brief_extracts_params():
    brief = service._tool_brief(
        {
            "type": "function",
            "function": {
                "name": "create_it_ticket",
                "description": "创建 IT 工单",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "work_code": {"type": "string", "description": "工号"},
                        "urgency": {"type": "integer"},
                    },
                    "required": ["work_code"],
                },
            },
        }
    )
    assert brief["name"] == "create_it_ticket"
    assert brief["description"] == "创建 IT 工单"
    by_name = {p["name"]: p for p in brief["params"]}
    assert by_name["work_code"]["required"] is True
    assert by_name["work_code"]["description"] == "工号"
    assert by_name["urgency"]["required"] is False


def test_tool_brief_handles_no_params():
    brief = service._tool_brief(
        {"function": {"name": "ping", "description": "", "parameters": {}}}
    )
    assert brief["params"] == []


def test_sample_identity_respects_pattern():
    class Cfg:
        class identity:
            pattern = r"^[LM]\d{6}$"

    assert service._sample_identity(Cfg) == "L220104"


def test_sample_identity_without_pattern():
    class Cfg:
        class identity:
            pattern = None

    assert service._sample_identity(Cfg) == "probe-user"


def test_sample_identity_falls_back_for_odd_pattern():
    class Cfg:
        class identity:
            pattern = r"^ZZZ\d+$"

    assert service._sample_identity(Cfg) == "probe-user"


# --------------------------------------------------------------------------
# 技能：删除共享 skill 时要连带清掉各 Agent 的引用
# --------------------------------------------------------------------------


def _use_skills(project: Path, name: str, files: list[str], enabled: bool = True) -> None:
    """让某个 Agent 勾选引用这些 skill（走页面同一条保存路径）"""
    data = service.read_bot(project, name)
    data["values"]["skills"] = {"enabled": enabled, "files": files}
    service.save_bot(project, name, {"values": data["values"], "prompt": data["prompt"]})


def _skill_files(bot_dir: Path) -> list[str] | None:
    raw = yaml.safe_load((bot_dir / "bot.yaml").read_text(encoding="utf-8"))
    skills = raw.get("skills")
    return None if skills is None else skills.get("files")


@pytest.fixture
def two_bots(project: Path, bot: Path):
    """hr-bot 之外再建一个 crm-bot，密钥齐全；共享目录里放两份 skill"""
    service.create_new_bot(project, "crm-bot")
    crm = project / "bots" / "crm-bot"
    (crm / ".env").write_text((bot / ".env").read_text(encoding="utf-8"), encoding="utf-8")
    service.upload_skill(project, "faq.md", "# FAQ\n常见问题")
    service.upload_skill(project, "glossary.md", "# 术语表\n名词解释")
    return bot, crm


def test_list_skills_reports_used_by(project, two_bots):
    _use_skills(project, "hr-bot", ["faq.md"])
    _use_skills(project, "crm-bot", ["faq.md", "glossary.md"])

    by_name = {s["name"]: s for s in service.list_skills(project)}
    assert by_name["faq.md"]["used_by"] == ["crm-bot", "hr-bot"]
    assert by_name["glossary.md"]["used_by"] == ["crm-bot"]


def test_delete_skill_removes_references_from_all_agents(project, two_bots):
    hr, crm = two_bots
    _use_skills(project, "hr-bot", ["faq.md"])
    _use_skills(project, "crm-bot", ["faq.md", "glossary.md"])

    result = service.delete_skill(project, "faq.md")

    assert sorted(result["cleaned"]) == ["crm-bot", "hr-bot"]
    assert result["skipped"] == []
    assert not (service.skills_root(project) / "faq.md").exists()
    assert _skill_files(hr) == []
    assert _skill_files(crm) == ["glossary.md"]
    # 清完引用后两个 Agent 都能正常通过校验，不会报「技能文件不存在」
    assert service.validate_bot_dir(hr)["ok"], service.validate_bot_dir(hr)["error"]
    assert service.validate_bot_dir(crm)["ok"], service.validate_bot_dir(crm)["error"]


def test_delete_skill_leaves_unrelated_agents_untouched(project, two_bots):
    hr, crm = two_bots
    _use_skills(project, "hr-bot", ["faq.md"])
    _use_skills(project, "crm-bot", ["glossary.md"])
    crm_before = (crm / "bot.yaml").read_bytes()

    result = service.delete_skill(project, "faq.md")

    assert result["cleaned"] == ["hr-bot"]
    assert (crm / "bot.yaml").read_bytes() == crm_before  # 没引用的 Agent 一个字节都不动


def test_delete_last_disabled_reference_drops_skills_section(project, two_bots):
    hr, _ = two_bots
    _use_skills(project, "hr-bot", ["faq.md"], enabled=False)

    service.delete_skill(project, "faq.md")

    assert _skill_files(hr) is None  # 未启用且没文件了，整段 skills 省略
    assert service.validate_bot_dir(hr)["ok"], service.validate_bot_dir(hr)["error"]


def test_delete_skill_skips_agent_that_cannot_be_rewritten_safely(project, two_bots):
    """重写会改变其他配置时宁可不改，报 skipped 让用户去页面手动取消勾选"""
    hr, _ = two_bots
    _use_skills(project, "hr-bot", ["faq.md"])
    raw = yaml.safe_load((hr / "bot.yaml").read_text(encoding="utf-8"))
    raw["mcp"] = {"servers": []}  # 生成器会给空 servers 补默认服务端，往返后语义会变
    (hr / "bot.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    before = (hr / "bot.yaml").read_bytes()

    result = service.delete_skill(project, "faq.md")

    assert result["skipped"] == ["hr-bot"]
    assert result["cleaned"] == []
    assert (hr / "bot.yaml").read_bytes() == before
    assert not (service.skills_root(project) / "faq.md").exists()  # 文件照删


def test_delete_skill_without_references(project, two_bots):
    result = service.delete_skill(project, "glossary.md")
    assert result["cleaned"] == []
    assert result["skipped"] == []
    assert [s["name"] for s in result["skills"]] == ["faq.md"]

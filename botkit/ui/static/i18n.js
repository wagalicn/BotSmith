/*
 * botkit 配置页面 —— 轻量国际化（i18n）
 *
 * 没有框架、没有构建步骤，和页面其余部分一样「拿到目录就能跑」。
 *
 * 用法：
 *   t("key")              取当前语言的文案
 *   t("key", {a: 1})      带占位符替换，文案里写 {a}
 *   setLang("en")         切语言，存进 localStorage
 *   applyStaticI18n()     把带 data-i18n 的静态 DOM 填充成当前语言
 *   translateOption(...)  把后端返回的 options（含中文 label/note）按 value/key 翻成当前语言
 *
 * 约定：字典里英文用词与仓库的英文文档保持一致 —— Agent、External API、
 * WeCom、employee ID 等。
 */

"use strict";

const I18N = {
  zh: {
    // ---- 顶栏 / 侧栏 ----
    "app.title_suffix": "Agent 配置页面",
    "topbar.note": "只有本机能访问 · 改完记得点保存",
    "lang.switch": "EN",
    "sidebar.heading": "Agent",
    "sidebar.import": "导入",
    "sidebar.new": "新建",
    "sidebar.run_hint_prefix": "配置改完在命令行启动：",
    "sidebar.empty": "还没有 Agent，点「新建」",

    // ---- 内容区头部 ----
    "content.export": "导出 JSON",
    "content.delete": "删除这个 Agent",
    "empty.title": "先选一个 Agent",
    "empty.body": "从左边选一个，或者点「新建」建一个新的。",

    // ---- tab ----
    "tab.secrets": "密钥",
    "tab.llm": "大模型",
    "tab.tools": "工具",
    "tab.identity": "身份与安全",
    "tab.prompt": "提示词",
    "tab.skills": "技能",
    "tab.session": "会话",
    "tab.messages": "文案",
    "tab.intercepts": "前置拦截",
    "tab.api": "对外接口",
    "tab.preview": "预览测试",
    "tab.usage": "用量",
    "tab.advanced": "其他",

    // ---- 用量 ----
    "usage.h2": "用量统计",
    "usage.desc": "每轮对话的 token 消耗按月记录在本机（bots/该Agent/usage/，含用户标识、不进 git）。这里只读查看，数据随对话自动累积。",
    "usage.month_label": "月份",
    "usage.empty": "还没有任何用量记录。和这个 Agent 对话（企微 / 对外接口 / 预览）后，这里会出现本月数据。",
    "usage.card_turns": "对话数",
    "usage.card_total_tokens": "总 Token",
    "usage.card_io_tokens": "输入 / 输出",
    "usage.card_tool_calls": "工具调用",
    "usage.card_success_rate": "成功率",
    "usage.card_avg_elapsed": "平均耗时",
    "usage.detail_h3": "明细",
    "usage.detail_hint": "最新在前。问题内容按原样记录，敏感场景可在 Agent 的 hooks.py 里自行处理。",
    "usage.col_ts": "时间",
    "usage.col_identity": "用户",
    "usage.col_source": "来源",
    "usage.col_model": "模型",
    "usage.col_in": "输入",
    "usage.col_out": "输出",
    "usage.col_total": "合计",
    "usage.col_tools": "工具",
    "usage.col_elapsed": "耗时",
    "usage.col_ok": "结果",
    "usage.col_question": "问题",
    "usage.ok_yes": "成功",
    "usage.ok_no": "失败",
    "usage.ms": "{n} ms",
    "usage.load_fail": "读取用量失败：{error}",

    // ---- 密钥 ----
    "secrets.h2": "密钥",
    "secrets.desc":
      "这些值来自企微后台和平台管理员，存在本 bot 目录的 .env 里，不会进 git。" +
      "下面的条目是根据配置里用到的占位符自动列出来的。" +
      "如果这个 Agent 只做对外接口（不连企业微信），企微 bot_id / bot_secret 可以留空 —— 去「对外接口」页开启 chat 接口即可。",
    "secrets.toggle_show": "显示明文",
    "secrets.toggle_hide": "隐藏明文",
    "secrets.probe_h3": "验证企微连接",
    "secrets.probe_desc":
      "先确认凭证对不对，再确认 Agent 能不能认出说话的人是谁 —— 这两步是新建 bot 最容易卡住的地方。",
    "secrets.test_cred": "测试凭证",
    "secrets.wait_at": "等我在企微 @ 一句话",
    "secrets.empty": "这个配置没有用到 ${} 占位符。",
    "secrets.not_filled": "还没填",
    "secrets.wecom_btn_hint": "请先在上方填写 WECHAT_BOT_ID 和 WECHAT_BOT_SECRET",
    "env.WECHAT_BOT_ID": "企微后台 → 智能机器人 → API模式 → 长连接",
    "env.WECHAT_BOT_SECRET": "同上，和 bot_id 一起给出",
    "env.LLM_API_URL": "用 openai 后端时填到 /v1 为止，不要带 /chat/completions",
    "env.LLM_API_KEY": "大模型平台的密钥",
    "env.LLM_MODEL": "模型名，如 qwen3.7-plus",
    "env.LLM_HTTP_PROXY": "可选。访问大模型用的 HTTP 代理，不用就留空",

    // ---- 大模型 ----
    "llm.h2": "大模型",
    "llm.client_label": "接口方式",
    "llm.client_note": "不确定就用推荐的那个",
    "llm.url_label": "接口地址（api_url）",
    "llm.url_note": "直接填明文，会存进本地 .env、不进 git。openai 后端填到 /v1 为止，不要带 /chat/completions",
    "llm.key_label": "接口密钥（api_key）",
    "llm.key_note": "大模型平台的密钥。存进本地 .env、不进 git",
    "llm.model_label": "模型（model）",
    "llm.model_note": "模型名，直接填明文。存进本地 .env、不进 git",
    "llm.max_tokens_label": "单次回复最多生成（token）",
    "llm.max_tokens_note":
      "留空 = 不限制，由端点默认值决定。各模型上限差别很大：" +
      "旧 Claude 3 只有 4096，Claude 3.5 是 8192，当前新模型可到 128000。" +
      "超过模型上限会直接报错。anthropic 协议必须带这个参数，留空时按 8192 发送",
    "llm.anthropic_warn":
      "anthropic 协议（Claude 原生 Messages API）：地址填到 /v1 为止，" +
      "后端会自己拼 /messages。例如 Bedrock 填 " +
      "https://bedrock-mantle.<区域>.api.aws/anthropic/v1，" +
      "直连官方填 https://api.anthropic.com/v1。" +
      "用它的场景是端点只提供 Messages API（如 Bedrock 上的 Claude）",
    "llm.proxy_enable": "通过 HTTP 代理访问大模型",
    "llm.proxy_enable_desc":
      "端点有地区或网络限制时才需要。不勾选 = 直连，bot.yaml 里不会写这项配置",
    "llm.proxy_label": "代理地址",
    "llm.proxy_note":
      "格式 http://host:port，需要认证就 http://user:pass@host:port。" +
      "两个后端都支持。值存进本地 .env、不进 git",
    "llm.timeout_label": "单次请求超时（秒）",
    "llm.timeout_note": "带工具调用的一轮对话会请求多次，总耗时可能是它的几倍",
    "llm.rounds_label": "最多连续调几轮工具",
    "llm.rounds_note": "防止模型反复调工具打转。到上限会回「工具轮次用尽」的文案",
    "llm.show_key": "显示密钥明文",
    "llm.hide_key": "隐藏密钥明文",
    "llm.aiohttp_warn":
      "兼容模式下地址要填完整端点（含 /chat/completions）。这个模式没有自动重试，一般只在官方 SDK 报 pydantic 校验错时才用。",

    // ---- 工具 ----
    "tools.h2": "工具",
    "tools.desc":
      "Agent 的能力来自 MCP 服务端提供的工具。可以接多个服务端 —— 比如同时连 IT 工单中台和 CRM。" +
      "每个服务端配好后点「探测」，把它实际有的工具列出来，勾上这个 Agent 需要用的。勾得越少模型越不容易选错，也越省 token。",
    "tools.servers_h3": "MCP 服务端",
    "tools.add_server": "＋ 添加一个服务端",
    "tools.add_server_hint": "地址直接填明文，会存进本地 .env、不进 git",
    "tools.probe_all": "探测全部服务端的工具",
    "tools.probe_need_save": "需要先保存并通过校验",
    "tools.no_servers": "还没有 MCP 服务端。点下面「添加」加一个。",
    "tools.server_n": "服务端 {n}",
    "tools.delete": "删除",
    "tools.delete_server_title": "删除这个服务端",
    "tools.field_name_label": "标识名",
    "tools.field_name_note": "自取，如 oa、crm。用于日志和工具归属",
    "tools.field_addr_label": "地址",
    "tools.field_addr_note": "MCP 服务端 URL，如 https://your-host:8081/mcp。直接填明文即可 —— 它会存进本地 .env、不进 git。",
    "tools.field_header_label": "身份透传头",
    "tools.field_header_note": "把当前用户身份透传给服务端的请求头名。留空则不发。默认 X-MCP-Identity",
    "tools.extra_headers_label": "额外请求头",
    "tools.extra_headers_note": "服务端要求鉴权时填，比如头名 Authorization、值 Bearer xxx。值会存进本地 .env、不进 git。",
    "tools.add_header": "＋ 添加请求头",
    "tools.probe_only_this": "只探测这个",
    "tools.probe_only_need_name": "先填标识名",
    "tools.not_probed": "还没探测。已配置的白名单：",
    "tools.allow_empty": "（空 = 全部工具都给模型）",
    "tools.server_colon": "服务端：{name}",
    "tools.unnamed": "(未命名)",
    "tools.provide_selected": "服务端提供 {total} 个，已勾选 {count} 个",
    "tools.select_all": "全选",
    "tools.select_none": "全不选",
    "tools.missing_tools": "配置里这些工具服务端上没有，可能是拼错或中台改名了：{names}",
    "tools.no_params": "无参数",
    "tools.no_desc": "（这个工具没写描述，模型会不太好选）",
    "tools.params_prefix": "参数：{params}",
    "tools.confirm_delete_server": "删除服务端「{name}」？它勾选的工具也会一起去掉。",

    // ---- 身份 ----
    "identity.h2": "身份与安全",
    "identity.source_label": "怎么确定「是谁在说话」",
    "identity.pattern_label": "工号格式校验",
    "identity.pattern_note":
      "格式不符的人会被直接拒绝，不会进模型也不会碰工具。不确定就先选「不校验」，用密钥页的「等我 @ 一句话」看清真实工号长什么样再回来配。",
    "identity.custom_regex_option": "自定义正则…",
    "identity.custom_regex_label": "自定义正则",
    "identity.custom_regex_note": "Python 正则语法，例如 ^[LM]\\d{6}$",
    "identity.inject_h3": "防止模型替别人操作",
    "identity.inject_desc":
      "模型会自己编参数。如果某个工具有「这是谁提的」这类参数（工单的提交人、报销的申请人），一定要在这里绑定成「当前用户」。" +
      "绑定之后，框架会在真正调用前用企微那边拿到的身份覆盖掉模型填的值。",
    "identity.add_inject": "添加一条绑定",
    "identity.inject_hint_probe": "先去「工具」页点探测，这里就能直接从真实参数里选",
    "identity.inject_empty": "还没有绑定。如果工具里有「提交人」「申请人」这类参数，强烈建议加一条。",
    "identity.tool_aria": "工具",
    "identity.arg_aria": "参数",
    "identity.pick_tool": "选一个工具…",
    "identity.pick_arg": "选一个参数…",
    "identity.inject_mid": "的参数",
    "identity.inject_tail": "= 当前用户",
    "identity.inject_delete": "删除",

    // ---- 提示词 ----
    "prompt.h2": "提示词",
    "prompt.desc":
      "写清这个 Agent 是谁、要做什么、按什么顺序调工具、不许做什么。这一步决定 Agent 好不好用，比调任何参数都重要。" +
      "约束用「严禁」「必须」这类硬词比「建议」有效。文件后缀是 .md，但不强制 Markdown 语法 —— 整段文本会原样交给模型，右边的预览只是帮你看排版。",
    "prompt.view_rendered": "预览",
    "prompt.view_check": "结构检查",
    "prompt.empty_preview": "还没写提示词。左边写点东西，这里会实时预览。",
    "prompt.meta": "{chars} 字 · 保存到 {file}",
    "prompt.check_score": "结构完整度 {score}/{total} · 共 {chars} 字",
    "prompt.check_can_add": "可以再补充：",
    "prompt.check_ok": "结构挺完整了。真正好不好用，去「预览测试」里聊两句看看。",
    // prompt_check 的检查项（按后端稳定 key 翻译 label/hint）
    "check.role.label": "角色定位",
    "check.role.hint": "写清「你是谁」，比如“你是 HR 助手，运行在企业微信里”。",
    "check.task.label": "核心任务",
    "check.task.hint": "写清这个 Agent 要帮用户做什么。",
    "check.reply.label": "回答要求",
    "check.reply.hint": "给点回答风格上的约束，比如“用中文简洁回答”“信息不全时主动追问”。",
    "check.flow.label": "工具调用流程",
    "check.flow.hint": "按顺序调工具的 bot，把「先调什么、再调什么」写成编号步骤，模型更不容易乱。",
    "check.constraint.label": "硬性约束",
    "check.constraint.hint": "用“严禁/必须”这类硬词写清不许做什么，比如“严禁自己编造 ID，必须来自查询结果”。",
    // suggestions（前端根据 items/字数自行生成，不再用后端拼好的中文）
    "check.sug.empty": "提示词还是空的。至少写清角色和任务，Agent 才知道自己该干嘛。",
    "check.sug.short": "提示词有点短。多写几句角色、任务和约束，效果会明显好很多。",
    "check.sug.no_heading": "没有用 # 分段。不是必须，但用“# 角色 / # 任务 / # 回答要求”分段，自己和模型都更好读。",
    "check.sug.missing": "建议补充【{label}】：{hint}",

    // ---- 技能 ----
    "skills.h2": "技能",
    "skills.desc":
      "技能是补充给 Agent 的说明文档（业务规则、术语表、FAQ 等）。启用后，勾选的文档会拼进系统提示词，Agent 遇到需要理解的说明就能参考。" +
      "文档存在共享目录里，多个 Agent 可以各自勾选需要的那几份。",
    "skills.enable": "启用技能",
    "skills.enable_desc":
      "启用后，勾选的技能文档会拼进系统提示词，帮 Agent 理解业务说明、术语。文件放在共享目录 bots/_skills/，多个 Agent 可引用同一份。",
    "skills.empty": "共享目录还没有技能文件。用下面的按钮上传一个 .md。",
    "skills.delete_title": "从共享目录删除这个技能文件",
    "skills.empty_file": "（空文件）",
    "skills.chars_used": "{chars} 字 · 被 {users} 引用",
    "skills.chars_only": "{chars} 字",
    "skills.stale_warn": "这些技能文件已经不在共享目录里了，但当前 Agent 还引用着，会导致校验失败。移除引用后记得保存。",
    "skills.file_missing": "文件不存在",
    "skills.remove_ref": "移除引用",
    "skills.upload": "＋ 上传技能 .md",
    "skills.uploaded": "已上传技能 {name}",
    "skills.read_fail": "读文件失败",
    "skills.confirm_delete_used": "从共享目录删除技能「{name}」？\n\n这些 Agent 引用了它，会同时移除它们的引用：{users}",
    "skills.confirm_delete_unused": "从共享目录删除技能「{name}」？\n\n目前没有 Agent 引用它。",
    "skills.deleted": "已删除技能 {name}",
    "skills.deleted_cleaned": "，已从 {users} 移除引用",
    "skills.deleted_skipped": "；{users} 没能自动改写，请到对应 Agent 的「技能」页移除引用后保存",

    // ---- 会话 ----
    "session.h2": "会话上下文",
    "session.scope_label": "谁跟谁共享对话上下文",
    "session.max_turns_label": "记住最近几轮",
    "session.max_turns_note": "1 轮 = 一问一答。调大会让每次请求携带的历史变长，token 成本上升",
    "session.ttl_label": "多久没说话就忘掉（秒）",
    "session.ttl_note": "1800 = 半小时",

    // ---- 文案 ----
    "messages.h2": "文案",
    "messages.desc": "Agent 在各种情况下说的话。留空则用框架默认值。",
    "messages.keywords_h3": "重置关键词",
    "messages.keywords_desc": "用户发这些词会清空对话。整条消息完全等于其中一个才算命中。",
    "messages.progress_h3": "工具进度提示",
    "messages.progress_desc": "调用工具前推给用户的提示。工具慢的时候体验差别很大。",
    "messages.add_keyword": "添加关键词",
    "messages.delete": "删除",
    "messages.progress_default_hint": "← 其他工具都用这句",
    "messages.progress_add_one": "为某个工具单独写一句",

    // ---- 前置拦截 ----
    "intercepts.h2": "前置拦截",
    "intercepts.desc":
      "用户消息命中规则时，直接回固定文案，不进大模型 —— 快、省 token、答案稳定。适合帮助菜单、常见问答、固定话术。" +
      "命中优先级：重置关键词 > 拦截规则 > 大模型。",
    "intercepts.empty": "还没有拦截规则。点下面「添加规则」加一条。",
    "intercepts.rule_n": "规则 {n}",
    "intercepts.delete": "删除",
    "intercepts.match_label": "关键词（精确匹配，逗号分隔）",
    "intercepts.match_note": "整条消息完全等于其中一个词才命中",
    "intercepts.match_placeholder": "帮助, help, ?",
    "intercepts.regex_label": "正则匹配（可选）",
    "intercepts.regex_note": "命中子串即算。和关键词至少填一个",
    "intercepts.regex_placeholder": "^在吗[?？]?$",
    "intercepts.reply_label": "命中后的回复",
    "intercepts.reply_note": "支持多行",
    "intercepts.reply_placeholder": "命中时 Agent 直接回这句话",
    "intercepts.add_rule": "添加规则",

    // ---- 对外接口 ----
    "api.h2": "对外接口",
    "api.desc":
      "把这个 Agent 开成一个 HTTP 接口（POST /chat），供 Dify、内部门户或其它系统调用。" +
      "可以和企业微信同时用，也可以只开接口、不连企微。调用方负责传 user（用户标识）和 session_id（会话 ID）。",
    "api.enable": "开启对外 chat 接口",
    "api.enable_desc":
      "开启后，运行 Agent 时会同时起一个 HTTP 服务，暴露 POST /chat 供 Dify 或自研门户调用。也可以只启动这个接口、不连企业微信（见下方启动说明）。",
    "api.port_label": "监听端口",
    "api.port_note": "chat 接口监听的端口，默认 9000。多个 bot 同机部署时各用不同端口",
    "api.token_label": "鉴权 token（API Key）",
    "api.token_note": "调用方需在请求头带 Authorization: Bearer <token>。留空 = 不鉴权（仅限可信内网）。存进本地 .env、不进 git",
    "api.show_token": "显示 token 明文",
    "api.hide_token": "隐藏 token 明文",
    "api.how_h3": "怎么调用",

    // ---- 预览测试 ----
    "preview.h2": "预览测试",
    "preview.desc":
      "不连企业微信，直接在这儿跟 Agent 聊两句看效果。走的是和正式运行一模一样的流程 —— 真调大模型、真调 MCP 工具。" +
      "用来验证提示词写得对不对、工具选得全不全、身份注入生效没有。",
    "preview.warn":
      "注意：预览会真的执行工具。如果提示词触发了「创建工单」这类动作，这里会真建一张单。拿测试数据试，别用真实业务信息。",
    "preview.identity_label": "用哪个身份测试",
    "preview.identity_placeholder": "留空自动用一个示例工号",
    "preview.send": "发送",
    "preview.clear": "清空对话",
    "preview.input_placeholder": "输入一句话，比如：我想请三天年假",
    "preview.input_hint": "按 Enter 发送，Shift+Enter 换行。预览的对话不会保存。",
    "preview.empty": "还没开始。下面输入一句话发送试试。",
    "preview.args": "参数",
    "preview.result": "返回",
    "preview.truncated": "\n…（已截断）",
    "preview.thinking": "正在思考…（真调 LLM 和工具，可能要几秒）",
    "preview.confirm_dirty": "有配置改动还没保存。预览用的是已保存的配置，未保存的改动不会生效。\n\n继续预览？",

    // ---- 其他 ----
    "advanced.h2": "其他",
    "advanced.log_label": "日志详细程度",
    "advanced.log_default": "（默认）",
    "advanced.log_debug": "（排查问题时用）",
    "advanced.hooks_h3": "代码扩展点",
    "advanced.summary_h3": "当前配置摘要",
    "advanced.hooks_none": "没有 hooks.py。绝大多数 Agent 不需要写代码。",
    "advanced.hooks_fail": "hooks.py 加载失败：\n{error}",
    "advanced.hooks_commented": "{file} 里所有扩展点都是注释状态，当前不生效。这是正常的。",
    "advanced.hooks_active": "{file} 已启用的扩展点：{list}",

    // ---- 底部动作条 ----
    "action.revalidate": "重新校验",
    "action.save": "保存",
    "status.placeholder": "—",

    // ---- 对话框：新建 ----
    "dialog.new_h2": "新建 Agent",
    "dialog.new_name_label": "名字",
    "dialog.new_name_hint": "会作为目录名。字母开头，只能用字母、数字、下划线、连字符。",
    "dialog.cancel": "取消",
    "dialog.new_submit": "创建",
    "dialog.new_need_name": "请输入名字",

    // ---- 对话框：导入 ----
    "dialog.import_h2": "导入 Agent",
    "dialog.import_hint": "粘贴一份导出的 Agent JSON，或选一个 .json 文件。导入只建配置结构，不含密钥 —— 导入后去「密钥」页填。",
    "dialog.import_file_label": "从文件选择",
    "dialog.import_text_label": "或直接粘贴 JSON",
    "dialog.import_submit": "导入",
    "dialog.import_need_text": "先粘贴 JSON 或选一个文件",
    "dialog.import_bad_json": "这不是合法的 JSON：{error}",
    "dialog.import_read_fail": "读文件失败",

    // ---- 状态 / 通用提示 ----
    "status.dirty": "有未保存的改动",
    "status.reading": "正在读取…",
    "status.saving": "正在保存…",
    "status.validating": "正在校验…",
    "status.deleting": "正在删除…",
    "status.exporting": "正在导出…",
    "status.importing": "正在导入…",
    "status.validate_ok": "配置校验通过",
    "status.validate_bad": "配置有问题，看上方提示",
    "status.created": "已创建，先在「密钥」页填企微凭证和 MCP 地址",
    "status.imported": "已导入，去「密钥」页填企微凭证和 MCP 地址/token",
    "status.deleted": "已删除「{name}」（在回收站里可找回）",
    "status.exported": "已导出 JSON（不含密钥值，导入后需在密钥页填）",
    "status.init_fail": "初始化失败：{error}",
    "banner.validate_fail": "配置校验没通过：\n\n{error}",
    "summary.none": "（校验未通过，没有摘要）",

    "api.non_json": "服务端返回了非 JSON 内容（HTTP {status}）",
    "api.req_fail": "请求失败（HTTP {status}）",

    "confirm.switch_dirty": "有改动还没保存，切换会丢掉。确定切换吗？",
    "confirm.delete_bot": "确定删除 Agent「{name}」吗？\n\n它会被移到 bots/.trash/ 回收站，不是彻底删除，误删了可以从那里找回。",

    // ---- MCP / 企微探测 ----
    "mcp.saving_first": "检测到未保存的改动，正在先保存…",
    "mcp.save_fail": "保存没成功，先按上面的提示把配置改对再探测。",
    "mcp.connecting_one": "正在连接服务端「{name}」…",
    "mcp.connecting_all": "正在连接全部 MCP 服务端…",
    "mcp.ok": "{name}：连通，拿到 {count} 个工具",
    "mcp.bad": "{name} 连不上：\n{error}",
    "mcp.identity_used": "探测时使用的身份：{identity}",
    "wecom.waiting": "已连上，请现在切到企业微信，@ 这个 Agent 发一句话（最多等 90 秒）…",
    "wecom.connecting": "正在连接企业微信…",
    "wecom.cred_ok": "企微凭证正确，Agent 能连上。",
    "wecom.timed_out": "等了 90 秒没收到消息。检查：Agent 在群里吗？群里发消息时 @ 它了吗？",
    "wecom.kv_userid": "识别到的用户 ID",
    "wecom.kv_encrypted": "看起来像加密串吗",
    "wecom.kv_chattype": "会话类型",
    "wecom.kv_content": "你说的话",
    "wecom.kv_stripped": "去掉 @ 之后",
    "wecom.kv_session": "会话隔离 key",
    "wecom.encrypted_yes": "像（需要映射）",
    "wecom.encrypted_no": "不像，是明文",
    "wecom.chat_group": "群聊",
    "wecom.chat_single": "单聊",
    "wecom.kv_empty": "（空）",
    "wecom.identity_ok": "身份校验通过。这个用户能正常使用 Agent。",
    "wecom.identity_encrypted":
      "身份校验不通过，而且这个 ID 像加密串。\n\n两条路：\n1）（推荐）让超管重建企微机器人，一般就能拿到明文工号；\n2）在 hooks.py 里实现 resolve_identity 做「加密 ID → 工号」的映射，并把身份来源改成「自定义」。",
    "wecom.identity_pattern": "身份校验不通过：{userid} 匹配不上 {pattern}。\n这个 ID 看起来是明文的，多半是格式校验写严了 —— 去「身份与安全」页改一下。",
  },

  en: {
    // ---- topbar / sidebar ----
    "app.title_suffix": "Agent config",
    "topbar.note": "Local machine only · remember to save after editing",
    "lang.switch": "中文",
    "sidebar.heading": "Agent",
    "sidebar.import": "Import",
    "sidebar.new": "New",
    "sidebar.run_hint_prefix": "After editing, start from the command line:",
    "sidebar.empty": "No Agents yet, click \"New\"",

    // ---- content head ----
    "content.export": "Export JSON",
    "content.delete": "Delete this Agent",
    "empty.title": "Pick an Agent first",
    "empty.body": "Pick one on the left, or click \"New\" to create one.",

    // ---- tabs ----
    "tab.secrets": "Keys",
    "tab.llm": "Model",
    "tab.tools": "Tools",
    "tab.identity": "Identity & Security",
    "tab.prompt": "Prompt",
    "tab.skills": "Skills",
    "tab.session": "Session",
    "tab.messages": "Copy",
    "tab.intercepts": "Intercepts",
    "tab.api": "External API",
    "tab.preview": "Preview test",
    "tab.usage": "Usage",
    "tab.advanced": "Other",

    // ---- usage ----
    "usage.h2": "Usage stats",
    "usage.desc": "Per-turn token usage is recorded locally by month (bots/<agent>/usage/, contains user identity, not committed to git). This view is read-only; data accrues automatically as conversations happen.",
    "usage.month_label": "Month",
    "usage.empty": "No usage records yet. After you chat with this Agent (WeCom / external API / preview), this month's data will show up here.",
    "usage.card_turns": "Turns",
    "usage.card_total_tokens": "Total tokens",
    "usage.card_io_tokens": "Input / Output",
    "usage.card_tool_calls": "Tool calls",
    "usage.card_success_rate": "Success rate",
    "usage.card_avg_elapsed": "Avg latency",
    "usage.detail_h3": "Details",
    "usage.detail_hint": "Newest first. Questions are stored as-is; handle sensitive cases in the Agent's hooks.py if needed.",
    "usage.col_ts": "Time",
    "usage.col_identity": "User",
    "usage.col_source": "Source",
    "usage.col_model": "Model",
    "usage.col_in": "In",
    "usage.col_out": "Out",
    "usage.col_total": "Total",
    "usage.col_tools": "Tools",
    "usage.col_elapsed": "Latency",
    "usage.col_ok": "Result",
    "usage.col_question": "Question",
    "usage.ok_yes": "OK",
    "usage.ok_no": "Fail",
    "usage.ms": "{n} ms",
    "usage.load_fail": "Failed to read usage: {error}",

    // ---- keys ----
    "secrets.h2": "Keys",
    "secrets.desc":
      "These values come from the WeCom console and the platform admin, stored in the .env in this bot's directory, not committed to git. " +
      "The entries below are listed automatically from the placeholders used in the config. " +
      "If this Agent only serves the external API (no WeCom), the WeCom bot_id / bot_secret can be left blank —— just enable the chat API on the \"External API\" tab.",
    "secrets.toggle_show": "Show plaintext",
    "secrets.toggle_hide": "Hide plaintext",
    "secrets.probe_h3": "Verify WeCom connection",
    "secrets.probe_desc":
      "First confirm the credentials are right, then confirm the Agent can recognize who is speaking —— these two steps are the easiest to get stuck on when creating a bot.",
    "secrets.test_cred": "Test credentials",
    "secrets.wait_at": "Wait for me to @ a line in WeCom",
    "secrets.empty": "This config uses no ${} placeholders.",
    "secrets.not_filled": "Not filled yet",
    "secrets.wecom_btn_hint": "Please fill in WECHAT_BOT_ID and WECHAT_BOT_SECRET above first",
    "env.WECHAT_BOT_ID": "WeCom console → Smart Robot → API mode → long connection",
    "env.WECHAT_BOT_SECRET": "Same as above, provided together with bot_id",
    "env.LLM_API_URL": "For the openai backend, go up to /v1, without /chat/completions",
    "env.LLM_API_KEY": "The model platform's key",
    "env.LLM_MODEL": "The model name, e.g. qwen3.7-plus",
    "env.LLM_HTTP_PROXY": "Optional. HTTP proxy for reaching the model; leave empty if unused",

    // ---- model ----
    "llm.h2": "Model",
    "llm.client_label": "Backend",
    "llm.client_note": "If unsure, use the recommended one",
    "llm.url_label": "API URL (api_url)",
    "llm.url_note": "Fill plaintext directly, stored in the local .env, not in git. For the openai backend, go up to /v1, without /chat/completions",
    "llm.key_label": "API key (api_key)",
    "llm.key_note": "The model platform's key. Stored in the local .env, not in git",
    "llm.model_label": "Model (model)",
    "llm.model_note": "The model name, fill plaintext directly. Stored in the local .env, not in git",
    "llm.max_tokens_label": "Max tokens per reply",
    "llm.max_tokens_note":
      "Empty = no limit, the endpoint's default applies. Ceilings vary a lot by model: " +
      "legacy Claude 3 allows 4096, Claude 3.5 allows 8192, current models reach 128000. " +
      "Exceeding a model's ceiling fails the request. The anthropic protocol requires this " +
      "parameter, so 8192 is sent when left empty",
    "llm.anthropic_warn":
      "anthropic protocol (Claude's native Messages API): set the address up to /v1, " +
      "the backend appends /messages itself. For Bedrock use " +
      "https://bedrock-mantle.<region>.api.aws/anthropic/v1, " +
      "for the official API use https://api.anthropic.com/v1. " +
      "Use this when the endpoint only offers the Messages API (e.g. Claude on Bedrock)",
    "llm.proxy_enable": "Reach the model through an HTTP proxy",
    "llm.proxy_enable_desc":
      "Only needed when the endpoint has regional or network restrictions. " +
      "Unchecked = direct connection, and this setting isn't written to bot.yaml",
    "llm.proxy_label": "Proxy address",
    "llm.proxy_note":
      "Format http://host:port, or http://user:pass@host:port if it needs auth. " +
      "Both backends support it. Stored in the local .env, not in git",
    "llm.timeout_label": "Per-request timeout (seconds)",
    "llm.timeout_note": "A turn with tool calls makes multiple requests, so the total time may be several times this",
    "llm.rounds_label": "Max consecutive tool rounds",
    "llm.rounds_note": "Prevents the model from looping on tool calls. At the limit it returns the \"tool rounds exceeded\" copy",
    "llm.show_key": "Show key plaintext",
    "llm.hide_key": "Hide key plaintext",
    "llm.aiohttp_warn":
      "In compatible mode the address must be the full endpoint (including /chat/completions). This mode has no auto-retry, usually only used when the official SDK throws a pydantic validation error.",

    // ---- tools ----
    "tools.h2": "Tools",
    "tools.desc":
      "An Agent's capabilities come from the tools MCP servers provide. You can connect multiple servers —— e.g. an IT ticket platform and CRM at once. " +
      "After configuring each server, click \"Probe\" to list the tools it actually has, and check the ones this Agent needs. The fewer you check, the less likely the model picks wrong, and the fewer tokens used.",
    "tools.servers_h3": "MCP servers",
    "tools.add_server": "＋ Add a server",
    "tools.add_server_hint": "Fill the address in plaintext, stored in the local .env, not in git",
    "tools.probe_all": "Probe all servers' tools",
    "tools.probe_need_save": "Must save and pass validation first",
    "tools.no_servers": "No MCP servers yet. Click \"Add\" below to add one.",
    "tools.server_n": "Server {n}",
    "tools.delete": "Delete",
    "tools.delete_server_title": "Delete this server",
    "tools.field_name_label": "Identifier",
    "tools.field_name_note": "Your choice, e.g. oa, crm. Used for logs and tool attribution",
    "tools.field_addr_label": "Address",
    "tools.field_addr_note": "The MCP server URL, e.g. https://your-host:8081/mcp. Fill plaintext directly —— it's stored in the local .env, not in git.",
    "tools.field_header_label": "Identity pass-through header",
    "tools.field_header_note": "The request-header name used to pass the current user's identity to the server. Blank = don't send. Default X-MCP-Identity",
    "tools.extra_headers_label": "Extra request headers",
    "tools.extra_headers_note": "Fill in when the server requires auth, e.g. header Authorization, value Bearer xxx. Values are stored in the local .env, not in git.",
    "tools.add_header": "＋ Add a header",
    "tools.probe_only_this": "Probe just this one",
    "tools.probe_only_need_name": "Fill the identifier first",
    "tools.not_probed": "Not probed yet. Configured whitelist:",
    "tools.allow_empty": "(empty = all tools given to the model)",
    "tools.server_colon": "Server: {name}",
    "tools.unnamed": "(unnamed)",
    "tools.provide_selected": "server provides {total}, {count} selected",
    "tools.select_all": "Select all",
    "tools.select_none": "Select none",
    "tools.missing_tools": "These configured tools aren't on the server, maybe a typo or renamed on the platform: {names}",
    "tools.no_params": "no parameters",
    "tools.no_desc": "(this tool has no description, the model may struggle to pick it)",
    "tools.params_prefix": "Parameters: {params}",
    "tools.confirm_delete_server": "Delete server \"{name}\"? Its selected tools will be removed too.",

    // ---- identity ----
    "identity.h2": "Identity & Security",
    "identity.source_label": "How to decide \"who is speaking\"",
    "identity.pattern_label": "Employee ID format check",
    "identity.pattern_note":
      "People whose format doesn't match are rejected outright, not entering the model or touching tools. If unsure, pick \"don't validate\" first, use \"Wait for me to @ a line\" on the Keys page to see what the real employee ID looks like, then come back.",
    "identity.custom_regex_option": "Custom regex…",
    "identity.custom_regex_label": "Custom regex",
    "identity.custom_regex_note": "Python regex syntax, e.g. ^[LM]\\d{6}$",
    "identity.inject_h3": "Prevent the model from acting as someone else",
    "identity.inject_desc":
      "The model makes up arguments on its own. If a tool has a \"who submitted this\" kind of parameter (a ticket's submitter, an expense's applicant), be sure to bind it to \"the current user\" here. " +
      "Once bound, the framework overwrites the model's value with the identity obtained on the WeCom side before the actual call.",
    "identity.add_inject": "Add a binding",
    "identity.inject_hint_probe": "Go to the \"Tools\" tab and probe, then you can pick from the real parameters here",
    "identity.inject_empty": "No bindings yet. If a tool has a \"submitter\" / \"applicant\" parameter, adding one is strongly recommended.",
    "identity.tool_aria": "Tool",
    "identity.arg_aria": "Parameter",
    "identity.pick_tool": "Pick a tool…",
    "identity.pick_arg": "Pick a parameter…",
    "identity.inject_mid": "'s parameter",
    "identity.inject_tail": "= the current user",
    "identity.inject_delete": "Delete",

    // ---- prompt ----
    "prompt.h2": "Prompt",
    "prompt.desc":
      "Write clearly who this Agent is, what it does, in what order to call tools, and what it must not do. This step decides whether the Agent is good to use, more than tuning any parameter. " +
      "Hard words like \"never\" / \"must\" work better than \"suggest\". The file ends in .md, but Markdown syntax isn't required —— the whole text is handed to the model as-is, and the preview on the right just helps you check layout.",
    "prompt.view_rendered": "Preview",
    "prompt.view_check": "Structure check",
    "prompt.empty_preview": "No prompt yet. Write something on the left and it previews here live.",
    "prompt.meta": "{chars} chars · saved to {file}",
    "prompt.check_score": "Structure completeness {score}/{total} · {chars} chars total",
    "prompt.check_can_add": "You could also add:",
    "prompt.check_ok": "The structure is fairly complete. Whether it's actually good, go to \"Preview test\" and chat a bit.",
    // prompt_check items (translated by the backend's stable key)
    "check.role.label": "Role definition",
    "check.role.hint": "State clearly \"who you are\", e.g. \"You are the HR assistant, running in WeCom\".",
    "check.task.label": "Core task",
    "check.task.hint": "State clearly what this Agent should help users do.",
    "check.reply.label": "Answer requirements",
    "check.reply.hint": "Give some constraints on answer style, e.g. \"answer concisely\" / \"ask proactively when info is incomplete\".",
    "check.flow.label": "Tool-calling flow",
    "check.flow.hint": "For a bot that calls tools in order, write \"call what first, then what\" as numbered steps so the model is less likely to get confused.",
    "check.constraint.label": "Hard constraints",
    "check.constraint.hint": "Use hard words like \"never/must\" to state what's forbidden, e.g. \"never fabricate an ID, it must come from the query result\".",
    // suggestions (generated on the frontend from items/length, no longer the backend's prebuilt text)
    "check.sug.empty": "The prompt is still empty. At least write the role and task so the Agent knows what to do.",
    "check.sug.short": "The prompt is a bit short. A few more lines on role, task, and constraints will help a lot.",
    "check.sug.no_heading": "No # sections. Not required, but splitting with \"# Role / # Task / # Answer requirements\" is easier to read for both you and the model.",
    "check.sug.missing": "Consider adding [{label}]: {hint}",

    // ---- skills ----
    "skills.h2": "Skills",
    "skills.desc":
      "Skills are supplementary docs for the Agent (business rules, glossary, FAQ, etc.). Once enabled, the checked docs are spliced into the system prompt, so the Agent can refer to them when it needs to understand something. " +
      "Docs live in a shared directory, and multiple Agents can each check the ones they need.",
    "skills.enable": "Enable skills",
    "skills.enable_desc":
      "Once enabled, the checked skill docs are spliced into the system prompt to help the Agent understand business descriptions and terminology. Files live in the shared directory bots/_skills/, and multiple Agents can reference the same one.",
    "skills.empty": "No skill files in the shared directory yet. Use the button below to upload a .md.",
    "skills.delete_title": "Delete this skill file from the shared directory",
    "skills.empty_file": "(empty file)",
    "skills.chars_used": "{chars} chars · referenced by {users}",
    "skills.chars_only": "{chars} chars",
    "skills.stale_warn": "These skill files are no longer in the shared directory, but this Agent still references them, which will fail validation. Remember to save after removing the references.",
    "skills.file_missing": "File does not exist",
    "skills.remove_ref": "Remove reference",
    "skills.upload": "＋ Upload skill .md",
    "skills.uploaded": "Uploaded skill {name}",
    "skills.read_fail": "Failed to read file",
    "skills.confirm_delete_used": "Delete skill \"{name}\" from the shared directory?\n\nThese Agents reference it, and their references will be removed too: {users}",
    "skills.confirm_delete_unused": "Delete skill \"{name}\" from the shared directory?\n\nNo Agent references it currently.",
    "skills.deleted": "Deleted skill {name}",
    "skills.deleted_cleaned": ", removed references from {users}",
    "skills.deleted_skipped": "; {users} couldn't be rewritten automatically, please remove the reference on that Agent's \"Skills\" tab and save",

    // ---- session ----
    "session.h2": "Conversation context",
    "session.scope_label": "Who shares conversation context with whom",
    "session.max_turns_label": "How many recent turns to remember",
    "session.max_turns_note": "1 turn = one Q&A. Raising it lengthens the history carried per request, increasing token cost",
    "session.ttl_label": "Forget after how long idle (seconds)",
    "session.ttl_note": "1800 = half an hour",

    // ---- copy ----
    "messages.h2": "Copy",
    "messages.desc": "What the Agent says in various situations. Leave blank to use the framework default.",
    "messages.keywords_h3": "Reset keywords",
    "messages.keywords_desc": "Users sending these words clear the conversation. The whole message must exactly equal one of them to match.",
    "messages.progress_h3": "Tool progress hints",
    "messages.progress_desc": "A hint pushed to the user before a tool call. It makes a big difference when a tool is slow.",
    "messages.add_keyword": "Add keyword",
    "messages.delete": "Delete",
    "messages.progress_default_hint": "← all other tools use this line",
    "messages.progress_add_one": "Write a separate line for a specific tool",

    // ---- intercepts ----
    "intercepts.h2": "Intercepts",
    "intercepts.desc":
      "When a user message matches a rule, reply with fixed copy directly, skipping the model —— fast, token-saving, stable answers. Good for a help menu, FAQs, fixed wording. " +
      "Match priority: reset keywords > intercept rules > the model.",
    "intercepts.empty": "No intercept rules yet. Click \"Add rule\" below to add one.",
    "intercepts.rule_n": "Rule {n}",
    "intercepts.delete": "Delete",
    "intercepts.match_label": "Keywords (exact match, comma-separated)",
    "intercepts.match_note": "The whole message must exactly equal one of them to match",
    "intercepts.match_placeholder": "help, ?",
    "intercepts.regex_label": "Regex match (optional)",
    "intercepts.regex_note": "A substring match counts. Fill at least one of keywords or regex",
    "intercepts.regex_placeholder": "^(hi|hello)$",
    "intercepts.reply_label": "Reply on match",
    "intercepts.reply_note": "Multi-line supported",
    "intercepts.reply_placeholder": "On match the Agent replies this directly",
    "intercepts.add_rule": "Add rule",

    // ---- external API ----
    "api.h2": "External API",
    "api.desc":
      "Expose this Agent as an HTTP API (POST /chat) for Dify, an internal portal, or any other system. " +
      "It can run alongside WeCom, or API-only without WeCom. The caller is responsible for passing user (user identifier) and session_id (conversation ID).",
    "api.enable": "Enable the external chat API",
    "api.enable_desc":
      "Once enabled, running the Agent also starts an HTTP service exposing POST /chat for Dify or a self-built portal. You can also start only this API without connecting to WeCom (see the startup notes below).",
    "api.port_label": "Listening port",
    "api.port_note": "The port the chat API listens on, default 9000. Use different ports when deploying multiple bots on one machine",
    "api.token_label": "Auth token (API Key)",
    "api.token_note": "The caller must send Authorization: Bearer <token> in the header. Blank = no auth (trusted intranet only). Stored in the local .env, not in git",
    "api.show_token": "Show token plaintext",
    "api.hide_token": "Hide token plaintext",
    "api.how_h3": "How to call",

    // ---- preview ----
    "preview.h2": "Preview test",
    "preview.desc":
      "Without connecting to WeCom, chat with the Agent right here to see how it does. It goes through exactly the same flow as production —— really calling the model and MCP tools. " +
      "Use it to verify the prompt is right, the tools are complete, and identity injection took effect.",
    "preview.warn":
      "Note: the preview actually executes tools. If the prompt triggers an action like \"create a ticket\", it really creates one here. Use test data, not real business information.",
    "preview.identity_label": "Which identity to test with",
    "preview.identity_placeholder": "Leave blank to auto-use a sample employee ID",
    "preview.send": "Send",
    "preview.clear": "Clear conversation",
    "preview.input_placeholder": "Type a line, e.g.: I'd like three days of annual leave",
    "preview.input_hint": "Press Enter to send, Shift+Enter for a new line. The preview conversation is not saved.",
    "preview.empty": "Not started yet. Type a line below and send to try.",
    "preview.args": "Parameters",
    "preview.result": "Result",
    "preview.truncated": "\n…(truncated)",
    "preview.thinking": "Thinking…(really calling the LLM and tools, may take a few seconds)",
    "preview.confirm_dirty": "There are unsaved config changes. The preview uses the saved config; unsaved changes won't take effect.\n\nContinue previewing?",

    // ---- other ----
    "advanced.h2": "Other",
    "advanced.log_label": "Log verbosity",
    "advanced.log_default": " (default)",
    "advanced.log_debug": " (for troubleshooting)",
    "advanced.hooks_h3": "Code extension points",
    "advanced.summary_h3": "Current config summary",
    "advanced.hooks_none": "No hooks.py. The vast majority of Agents don't need code.",
    "advanced.hooks_fail": "hooks.py failed to load:\n{error}",
    "advanced.hooks_commented": "All extension points in {file} are commented out, currently not in effect. This is normal.",
    "advanced.hooks_active": "Extension points enabled in {file}: {list}",

    // ---- bottom action bar ----
    "action.revalidate": "Re-validate",
    "action.save": "Save",
    "status.placeholder": "—",

    // ---- dialog: new ----
    "dialog.new_h2": "New Agent",
    "dialog.new_name_label": "Name",
    "dialog.new_name_hint": "Used as the directory name. Starts with a letter; only letters, digits, underscores, and hyphens.",
    "dialog.cancel": "Cancel",
    "dialog.new_submit": "Create",
    "dialog.new_need_name": "Please enter a name",

    // ---- dialog: import ----
    "dialog.import_h2": "Import Agent",
    "dialog.import_hint": "Paste an exported Agent JSON, or pick a .json file. Import only builds the config structure, no secrets —— fill them on the \"Keys\" tab after importing.",
    "dialog.import_file_label": "Pick from a file",
    "dialog.import_text_label": "Or paste JSON directly",
    "dialog.import_submit": "Import",
    "dialog.import_need_text": "Paste JSON or pick a file first",
    "dialog.import_bad_json": "This is not valid JSON: {error}",
    "dialog.import_read_fail": "Failed to read file",

    // ---- status / general ----
    "status.dirty": "Unsaved changes",
    "status.reading": "Reading…",
    "status.saving": "Saving…",
    "status.validating": "Validating…",
    "status.deleting": "Deleting…",
    "status.exporting": "Exporting…",
    "status.importing": "Importing…",
    "status.validate_ok": "Config validation passed",
    "status.validate_bad": "Config has problems, see the notice above",
    "status.created": "Created, fill WeCom credentials and the MCP address on the \"Keys\" tab first",
    "status.imported": "Imported, go to the \"Keys\" tab to fill WeCom credentials and the MCP address/token",
    "status.deleted": "Deleted \"{name}\" (recoverable from the trash)",
    "status.exported": "Exported JSON (no secret values, fill them on the Keys tab after importing)",
    "status.init_fail": "Initialization failed: {error}",
    "banner.validate_fail": "Config validation failed:\n\n{error}",
    "summary.none": "(validation didn't pass, no summary)",

    "api.non_json": "The server returned non-JSON content (HTTP {status})",
    "api.req_fail": "Request failed (HTTP {status})",

    "confirm.switch_dirty": "There are unsaved changes; switching will lose them. Switch anyway?",
    "confirm.delete_bot": "Delete Agent \"{name}\"?\n\nIt will be moved to the bots/.trash/ trash, not permanently deleted, so you can recover it from there if deleted by mistake.",

    // ---- MCP / WeCom probing ----
    "mcp.saving_first": "Detected unsaved changes, saving first…",
    "mcp.save_fail": "Save didn't succeed; fix the config per the notice above before probing.",
    "mcp.connecting_one": "Connecting to server \"{name}\"…",
    "mcp.connecting_all": "Connecting to all MCP servers…",
    "mcp.ok": "{name}: connected, got {count} tools",
    "mcp.bad": "{name} can't connect:\n{error}",
    "mcp.identity_used": "Identity used while probing: {identity}",
    "wecom.waiting": "Connected. Now switch to WeCom and @ this Agent with a line (waits up to 90 seconds)…",
    "wecom.connecting": "Connecting to WeCom…",
    "wecom.cred_ok": "WeCom credentials are correct, the Agent can connect.",
    "wecom.timed_out": "Waited 90 seconds with no message. Check: is the Agent in the group? Did you @ it when sending?",
    "wecom.kv_userid": "Recognized user ID",
    "wecom.kv_encrypted": "Looks like an encrypted string?",
    "wecom.kv_chattype": "Conversation type",
    "wecom.kv_content": "What you said",
    "wecom.kv_stripped": "After stripping the @",
    "wecom.kv_session": "Session isolation key",
    "wecom.encrypted_yes": "Yes (needs mapping)",
    "wecom.encrypted_no": "No, it's plaintext",
    "wecom.chat_group": "Group chat",
    "wecom.chat_single": "1:1 chat",
    "wecom.kv_empty": "(empty)",
    "wecom.identity_ok": "Identity validation passed. This user can use the Agent normally.",
    "wecom.identity_encrypted":
      "Identity validation failed, and this ID looks like an encrypted string.\n\nTwo paths:\n1) (Recommended) have a super admin recreate the WeCom robot, which usually yields a plaintext employee ID;\n2) implement resolve_identity in hooks.py to map \"encrypted ID → employee ID\", and change the identity source to \"custom\".",
    "wecom.identity_pattern": "Identity validation failed: {userid} doesn't match {pattern}.\nThis ID looks like plaintext, probably the format check is too strict —— change it on the \"Identity & Security\" tab.",
  },
};

// 后端 options 的翻译映射：用稳定的 value / key 作键，覆盖中文 label/note。
// 这样 service.py 不用改，前端按当前语言替换展示文案。
const OPTION_I18N = {
  en: {
    llm_clients: {
      openai: { label: "openai (recommended)", note: "Official SDK, with retries. Address goes up to /v1" },
      aiohttp: { label: "aiohttp (compatible mode)", note: "Raw HTTP, for non-standard compatible endpoints. Use the full endpoint address" },
    },
    session_scopes: {
      group_user: { label: "Per-person in a group (recommended)", note: "Two people in the same group don't interfere" },
      group: { label: "Whole group shares", note: "Several people discuss the same thing with the Agent together" },
      user: { label: "Per person, across groups", note: "The same person shares context across all conversations" },
    },
    identity_sources: {
      wecom_userid: { label: "WeCom userid (recommended)", note: "Use the user ID WeCom provides directly" },
      hook: { label: "Custom (hooks.py)", note: "When the userid isn't a business employee ID, look it up in hooks.py" },
    },
    identity_presets: {
      "": { label: "Don't validate" },
      "^[LM]\\d{6}$": { label: "L/M + 6 digits (e.g. L220104)" },
      "^[A-Za-z]\\d{6}$": { label: "A letter + 6 digits" },
      "^\\d+$": { label: "Digits-only employee ID" },
    },
    message_fields: {
      welcome: { label: "Welcome", note: "when the user enters the conversation" },
      empty_input: { label: "@'d without words", note: "remind the user to describe their need" },
      thinking: { label: "Processing hint", note: "a line sent first when processing begins" },
      reset_done: { label: "Reset done", note: "a reset keyword matched" },
      error: { label: "Error hint", note: "fallback when the model or a tool errors" },
      identity_invalid: { label: "Unrecognized identity", note: "employee ID format mismatch or resolution failure" },
      tool_rounds_exceeded: { label: "Tool rounds exceeded", note: "the model loops on tool calls" },
      reply_suffix: { label: "Reply suffix", note: "auto-appended to the end of every LLM reply (signature, etc.), blank to skip" },
    },
  },
};

let _lang = "zh";
try {
  const saved = localStorage.getItem("botkit_lang");
  if (saved === "zh" || saved === "en") _lang = saved;
} catch {
  // localStorage 不可用（隐私模式等）时就用默认中文，不报错
}

function getLang() {
  return _lang;
}

function setLang(lang) {
  if (lang !== "zh" && lang !== "en") return;
  _lang = lang;
  try {
    localStorage.setItem("botkit_lang", lang);
  } catch {
    // 忽略
  }
  document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
}

/** 取文案；缺键时回退到中文，再缺就原样返回 key（方便发现漏翻） */
function t(key, vars) {
  const table = I18N[_lang] || I18N.zh;
  let text = table[key];
  if (text === undefined) text = I18N.zh[key];
  if (text === undefined) return key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll("{" + name + "}", String(value));
    }
  }
  return text;
}

/**
 * 翻译后端 options 里某一项的 label/note。
 * group：options 的分组名（llm_clients / session_scopes / ...）
 * id：这一项的稳定键（value 或 key）
 * item：后端原始项（含中文 label/note），作为回退
 * 返回 {label, note}
 */
function translateOption(group, id, item) {
  if (_lang === "zh") {
    return { label: item.label, note: item.note };
  }
  const map = (OPTION_I18N[_lang] || {})[group] || {};
  const hit = map[id];
  return {
    label: hit && hit.label !== undefined ? hit.label : item.label,
    note: hit && hit.note !== undefined ? hit.note : item.note,
  };
}

/** 把带 data-i18n / data-i18n-* 属性的静态 DOM 填成当前语言 */
function applyStaticI18n(rootNode) {
  const scope = rootNode || document;
  // 文本内容
  for (const node of scope.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.getAttribute("data-i18n"));
  }
  // placeholder
  for (const node of scope.querySelectorAll("[data-i18n-placeholder]")) {
    node.setAttribute("placeholder", t(node.getAttribute("data-i18n-placeholder")));
  }
  // title
  for (const node of scope.querySelectorAll("[data-i18n-title]")) {
    node.setAttribute("title", t(node.getAttribute("data-i18n-title")));
  }
  // 文档标题
  const titleKey = document.querySelector("title[data-i18n]");
  if (titleKey) document.title = t(titleKey.getAttribute("data-i18n"));
}

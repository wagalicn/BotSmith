# botkit 后续规划（TODO）

记录尚未实现、但已想清方向的能力。核心目标：从「单个 bot」走向
「一个内部 bot 门户（ITbot 大全）」，让同事像用小程序一样挑选、使用各种定制 bot。

这份文档自包含，脱离原始讨论上下文也能看懂。

---

## 已完成的基线（现状）

- **配置框架**：`bot.yaml` + `prompt.md` 驱动，可视化配置页面（`python -m botkit ui`）。
- **企微长连接**：`python -m botkit run bots/<名>`。
- **对外 chat 接口**：`bot.yaml` 的 `api` 段开启后，`run` 会附带 HTTP `POST /chat`；
  也可 `python -m botkit serve-api bots/<名>` 只起接口、不连企微。
  - 契约：`POST /chat {message, user, session_id?}` → `{reply, kind, session_id}`；`GET /health`。
  - 鉴权：`Authorization: Bearer <token>`（`api.token`，走 `.env`，留空=不鉴权）。
  - `user` / `session_id` 由调用方（Dify/门户）提供，无状态透传。
  - 详见 `docs/配置参考.md` 的「api 对外接口」段。
- **技能(skill)**：共享目录 `bots/_skills/*.md`，bot 勾选引用，启用后拼进系统提示词。
- **导入/导出**：bot 配置可导出成 JSON（脱敏、不含密钥）、导入建新 bot。
- **密钥集中**：LLM 三项、MCP 地址/请求头、chat token 都走「明文填页面 → 占位符进 yaml → 值落 .env」。
- **多语言(i18n)**：配置页面 + 后端面向用户文案已中英双语（见下方第 6 项，已完成）。
- **门户防返工改造（已落地）**：`SessionStore` 已抽象成接口、`register_routes`/`create_api_app`
  已支持 `base_path` 前缀（见第 1 项说明）。为后续多 bot 网关、Redis 外置状态留好了口子。

---

## 待做（按优先级）

### 1. 一进程挂多个 Agent（多 Agent API 网关）  —— ✅ 已完成（2026-10）
**为什么**：门户后面会有一堆 Agent，一个 Agent 一个进程/端口不好管。
**做成什么**（已实现，路由形态以门户架构决策 §Q2 为准，走 `/{agent}/` 命名空间）：
- `python -m botkit serve-api bots/`：指向整个 bots 父目录，扫描所有开了
  `api.enabled` 的 Agent，一个进程、一个端口全挂起来。单 Agent 启动方式
  （`serve-api bots/<名>`）完全保留，网关是叠加能力。
- 路由：`POST /{agent}/chat`、`GET /{agent}/health`；网关级 `GET /agents`
  （列表 + 在线状态 + 错误原因）、`GET /health`（整体探活）。
- 鉴权按各 Agent 自己的 `api.token`；每个 Agent 独立 pipeline/engine/SessionStore；
  某个 Agent 配置坏了只标为不可用、不拖垮网关。
- 端口沿用 `--port > BOTKIT_API_PORT > 默认 9000`；网关进程可多开（多端口）
  交给 Nginx 做负载均衡，与单 Agent 多端口混用。
**落地位置**：
- `config.discover_api_bots` / `DiscoveredBot`（扫描 + 逐个加载，坏的不中断）。
- `api_server.build_gateway_app` / `GatewayAgent` / `_handle_agents` / 网关 `GET /health`
  （每个 Agent 用 `create_api_app(base_path="")` 建子 app + `add_subapp("/{agent}")`）。
- `runtime.build_gateway_from_dir` / `serve_api_gateway`（组装 + 起统一端口）。
- `cli.serve-api` 按路径分流：含 `bot.yaml`=单 Agent（现状不变），否则父目录=网关。
- 测试：`tests/test_discover.py`、`tests/test_gateway.py`、`tests/test_cli.py`（分流）。
**说明**：早期设想的 `POST /chat/<botname>` / `GET /bots` 命名已按门户架构决策统一为
`/{agent}/chat` / `GET /agents`。防返工前置（`SessionStore` 接口化、`base_path`）此前已就位。
**待续**：SSE 流式（见下）；门户页；RedisSessionStore（去掉会话亲和、随意扩缩）。

### 2. 流式回复（SSE）  —— 未做（接入点已就位）
**为什么**：聊天窗要「打字机」效果，等一整段返回体验差。
**做成什么**：`POST /chat` 支持 `Accept: text/event-stream`，逐段推 token。
**复用**：`pipeline.handle` 已有 `on_progress` 回调，可作为流式推送的接入点；
或在 engine 的 function calling 循环里加增量回调。
**现状（2026-10）**：
- ✅ `pipeline.handle(frame, on_progress=..., on_tool_call=...)` 已有回调，engine 循环已透传
  `on_progress`（企微那边已用它推进度消息）。
- ⬜ `api_server.py` 的 `/chat` 仍是一次性 `web.json_response`，没有 SSE 分支；
  真正的「逐 token 增量」还需要 LLM 后端支持流式输出（当前 `backend.complete` 是整段返回），
  这块要先确认两个后端能不能流式，再决定是走 token 级还是「进度+整段」级 SSE。

### 3. bot 注册表 + 聚合门户页（「ITbot 大全」）  —— 未做
**为什么**：给同事一个入口，列出所有 bot，点进去就能聊。
**做成什么**：
- 注册表：`registry.json` 或扫描 `bots/` 自动生成（名字、图标、简介、接口地址）。
- 门户前端：卡片列表 + 聊天窗，聊天窗后端就是调第 1 项的 `/chat/<botname>`。
- **账号与会话**：门户负责登录（账号=工号，作为 `user`）、为每个「用户+bot+会话窗」
  生成 `session_id` 并持久化。bot 侧的 SessionStore 只是短期上下文缓存，不负责会话生命周期。
**边界**：门户是独立项目/服务，bot 框架这边只需提供稳定的 `/chat` 契约。

### 4. 入口形态  —— 未做
- 桌面宠物 / 快捷方式 / 企微侧边栏：本质都是「打开门户 URL」的壳，不含 AI 逻辑。
- 优先级最低，等门户页稳定后再做。

### 5. 打包发布  —— 未做
- 现在靠源码目录 + `pip install -r requirements.txt`。
- 可选：`pyproject.toml` 打 wheel，或 PyInstaller 打 exe，方便分发。

### 6. 多语言支持（中 / 英，i18n）  —— ✅ 已完成（2026-10）
**为什么**：框架要开源，面向非中文用户也能用。
**范围**：配置页面 UI + 后端返回给用户的提示文案，中英双语。
不含：各 Agent 的对话文案（`bot.yaml` 的 `messages`，用户自填的业务内容，不由框架翻译）。
**实际做法**（最终落地，与开工前设想略有调整）：
- **前端**：自研极简 i18n，无构建步骤。
  - `static/i18n.js`：内置 `{zh, en}` 字典 + `t("key")` + `applyStaticI18n()`（填充 `data-i18n`）
    + `translateOption()`（翻译后端返回的 options label/note）。语言存 `localStorage`。
  - `index.html` 用 `data-i18n` 标注静态文案；`app.js` 全量用 `t()`；顶栏有中/EN 切换按钮，
    切换后重填静态 DOM + `render()` 重渲染，并对当前 bot 重新校验以刷新报错横幅/摘要。
  - 词典内置在 `i18n.js` 里（没拆成独立 json 文件），一样零构建、改一个文件即可。
- **后端**：没有走「错误码 + 参数」，而是**按请求 `Accept-Language` 返回对应语言文案**。
  - `config.py`：引入语言 `ContextVar`（默认 `zh`）+ `_m(zh, en)` 辅助 + `set/reset/get_message_lang`；
    所有 `ConfigError`、缺环境变量提示、`config_summary`（新增英文版）双语。
  - `ui/service.py`、`scaffold.py`、`api_server.py`：各自的 `UiError` / `ScaffoldError` / `/chat` 错误响应双语
    （复用同一个 ContextVar）。
  - `ui/server.py`：`lang_middleware` 按 `Accept-Language` 设/恢复语言；`api_server` 的 `/chat` 同样按头切换。
  - 前端 `api()` 请求带 `Accept-Language` 跟随界面语言。
- **关键保证**：CLI（`botkit validate/run/new`）和运行时不设置语言 → 永远中文，行为完全不变。
**已覆盖的后端文案**：`config.py` 全部 ConfigError + config_summary、`ui/service.py` 的 UiError、
`scaffold.py` 的 ScaffoldError、`api_server.py` 的 /chat 错误、`prompt_check` 的检查项（前端按 key 翻译）。
**测试**：新增中文默认 / `Accept-Language: en` 报错英文 / 摘要英文标签等用例；全套 pytest 全绿。
**遗留（可选，优先级低）**：
- 企微后台真实菜单名（如「智能机器人 → API模式 → 长连接」）是操作指引，英文文案里保留了中文原名/加注，没硬译。
- `runtime.py` 等纯日志（给运维看、不直接面向终端用户）仍是中文，未纳入 i18n 范围。

---

## 关键设计决策（沉淀，别推翻）

- **`/chat` 无状态**：`user` 和 `session_id` 一律由调用方提供，bot 不发号、不做账号体系。
  这样对接 Dify 和自研门户都成立。
- **身份**：若 bot 配了 `identity.pattern`，`user` 必须符合（如工号），否则被身份校验拦下。
  涉及「代表某人操作」的工具（如财务查个人报销）务必传真实工号。
- **安全边界**：`/chat` 服务只对话、不读写密钥/磁盘配置，可绑 `0.0.0.0` 暴露内网；
  而「配置页面」能读写密钥，只绑 `127.0.0.1`，两者取舍不同，别混。
- **密钥不进 git**：所有敏感值走 `.env`，`bot.yaml`/导出 JSON 只存占位符。

---

## 分离成独立项目时的注意

- `bot-demo/` 自包含，整目录复制即可用（见 `README.md` 的「整体带走」）。
- `bots/*/.env` 不提交；`bot.yaml` / `prompt.md` / `bots/_skills/*.md` 要提交。
- 命令都在 `bot-demo`（或分离后的项目根）目录下执行。

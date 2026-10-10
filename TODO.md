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

---

## 待做（按优先级）

### 1. 一进程挂多个 bot（多 bot API 网关）
**为什么**：门户后面会有一堆 bot，一个 bot 一个进程/端口不好管。
**做成什么**：
- `python -m botkit serve-api bots/`（指向整个 bots 目录，扫描所有开了 `api.enabled` 的 bot）。
- 路由改成 `POST /chat/<botname>`，一个端口服务多个 bot。
- `GET /bots` 返回可用 bot 列表（名字、简介、是否在线）。
**注意**：各 bot 的 SessionStore、引擎要隔离；一个 bot 配置坏了不能拖垮整个网关。

### 2. 流式回复（SSE）
**为什么**：聊天窗要「打字机」效果，等一整段返回体验差。
**做成什么**：`POST /chat` 支持 `Accept: text/event-stream`，逐段推 token。
**复用**：`pipeline.handle` 已有 `on_progress` 回调，可作为流式推送的接入点；
或在 engine 的 function calling 循环里加增量回调。

### 3. bot 注册表 + 聚合门户页（「ITbot 大全」）
**为什么**：给同事一个入口，列出所有 bot，点进去就能聊。
**做成什么**：
- 注册表：`registry.json` 或扫描 `bots/` 自动生成（名字、图标、简介、接口地址）。
- 门户前端：卡片列表 + 聊天窗，聊天窗后端就是调第 1 项的 `/chat/<botname>`。
- **账号与会话**：门户负责登录（账号=工号，作为 `user`）、为每个「用户+bot+会话窗」
  生成 `session_id` 并持久化。bot 侧的 SessionStore 只是短期上下文缓存，不负责会话生命周期。
**边界**：门户是独立项目/服务，bot 框架这边只需提供稳定的 `/chat` 契约。

### 4. 入口形态
- 桌面宠物 / 快捷方式 / 企微侧边栏：本质都是「打开门户 URL」的壳，不含 AI 逻辑。
- 优先级最低，等门户页稳定后再做。

### 5. 打包发布
- 现在靠源码目录 + `pip install -r requirements.txt`。
- 可选：`pyproject.toml` 打 wheel，或 PyInstaller 打 exe，方便分发。

### 6. 多语言支持（中 / 英，i18n）
**为什么**：框架要开源，面向非中文用户也能用。当前所有界面与提示都是中文硬编码。
**范围**：配置页面 UI + 后端返回给用户的提示文案，都要双语（中文 zh-CN / 英文 en）。
不含：各 Agent 的对话文案（`bot.yaml` 的 `messages`，那是用户自填的业务内容，不由框架翻译）。
**做成什么**：
- **前端（无构建约束）**：自研极简 i18n，不引 i18next 这类需打包的重库（保持「拿到目录就能跑」）。
  - 抽词条：`index.html` 用 `data-i18n="key"`，`app.js` 用 `t("key")` 取文案，替换掉现有硬编码中文。
  - 语言包：`static/i18n/zh-CN.json`、`static/i18n/en.json`（`{key: 文案}`）。
  - 顶栏加语言切换器；选择存 `localStorage`，默认跟随浏览器语言，回退 zh-CN。
  - 切换后走现有 `render()` 重渲染即可生效。
- **后端**：面向用户的中文串（`config.py` 的 `MessagesConfig` 默认值、`ConfigError`、`ui/service.py` 的
  `UiError`、`scaffold.py` 的 `ScaffoldError`、`api_server` 的错误响应等）国际化。
  - 推荐：后端返回**错误码 + 参数**，由前端按语言包映射文案（前后端只维护一份词条，最省事）；
    或后端按请求语言（`Accept-Language`）返回对应文案。二选一，开工前定。
**注意**：
- 词条抽取是主要工作量（UI 中文串约一两百处），框架逻辑几乎不动。
- 企微后台真实菜单名（如「智能机器人 → API模式 → 长连接」）是操作指引，英文包里应保留中文原名或加注，别硬译成用户在企微后台找不到的词。
- 与「机器人→Agent」的品牌统一保持一致：中英文包都用 "Agent"（不再出现「机器人」，企微产品名除外）。
- 测试里对中文错误串的断言（如 `找不到这个 Agent`）在改成错误码后要同步更新。
**独立性**：纯 UI/文案工程，与门户主线（1–4 项）无依赖，可随时插入。

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

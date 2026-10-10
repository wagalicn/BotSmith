# botkit —— API型 Agent 快捷搭建骨架

<p align="right"><a href="README.md">中文</a> | <a href="README.en.md">English</a></p>

用配置快速搭一个 Agent，并通过 HTTP 接口对外使用。业务定制集中在两个文件，
**不需要改 Python 代码**：

- `bot.yaml` —— LLM、MCP 工具、身份规则、会话策略、文案、对外接口
- `prompt.md` —— 这个 Agent 是谁、要做什么

搭好的 Agent 可以：

- **开成 HTTP 接口**（`POST /chat`）供 Dify、自研门户或任何第三方调用 —— 这是主要用法
- **接入企业微信**（长连接），直接在群里 @ 它对话 —— 另一种接入方式
- 两者可以**同时开**，也可以只开其中一个

**可视化配置**（推荐，尤其是给不熟命令行的同事）：

```
Windows   双击 start.bat
其他      ./start-ui.sh
```

浏览器会打开一个只有本机能访问的配置页面：新建/删除 Agent、填密钥、
**从真实工具列表里勾选**、绑定身份、写提示词（带实时预览和结构检查）、
挂技能文档、**开对外接口**、导入/导出配置、实时校验，还能**直接在页面里
跟 Agent 对话预览效果**（不依赖任何对外通道）。

**命令行**：

```bash
python -m botkit ui                            # 打开配置页面
python -m botkit new hr-assistant              # 生成骨架
python -m botkit validate bots/hr-assistant    # 校验配置（密钥自动打码）
python -m botkit serve-api bots/hr-assistant   # 只起对外 chat 接口（对接 Dify/门户，不连企微）
python -m botkit run bots/hr-assistant         # 连企微；若开了对外接口会一并起 /chat
```

两种方式可以混着用 —— 页面存盘会保留 `bot.yaml` 里的注释，
手改过的文件用页面打开也还是好的。

---

## 文档

| 看什么 | 去哪 |
|---|---|
| **第一次建 Agent** | [新建 Agent 步骤说明书](docs/新建Bot步骤说明书.md) —— 10 步，主线用配置页面，每步带预期结果和卡住了怎么办 |
| **查某个字段怎么配** | [配置参考](docs/配置参考.md) —— `bot.yaml` 全字段、hook、双后端、处理流程 |
| 这个项目是怎么回事 | 本文件 |

---

## 快速上手：开一个对外接口

最常见的用法 —— 把 Agent 开成 HTTP 接口，不连企微：

```bash
python -m botkit new my-agent                  # 生成骨架
# 在配置页填好 LLM/MCP 密钥、勾工具、写提示词、在「对外接口」页开开关
python -m botkit serve-api bots/my-agent       # 只起接口（Windows 可双击 start-api.bat）
```

接口契约：

```
POST http://<服务器IP>:<port>/chat
Header:  Authorization: Bearer <token>     # 配了 token 才需要
Body:    {"message": "用户说的话", "user": "L220104", "session_id": "conv-1"}
Resp:    {"reply": "Agent回复", "kind": "llm", "session_id": "conv-1"}

GET  http://<服务器IP>:<port>/health         # 探活，免鉴权
```

- **`user` / `session_id` 由调用方提供**：门户/Dify 传当前用户和会话 ID，
  Agent 无状态透传。同一 `session_id` 连续请求 = 连续多轮；不传则按 `user` 隔离。
- 接口绑 `0.0.0.0`（内网可达），只对话、不读写密钥，和「配置页面」（只绑本机）取舍不同。

字段和 Dify 对接细节见 [配置参考的 api 段](docs/配置参考.md#api-对外接口)。

**需要 `/chat` 之外的专属端点？** Agent 可以在自己的 `hooks.py` 里实现
`register_routes`，把自定义 HTTP 路由（结构化分析、批量导入等）挂到同一个服务上，
不改骨架。**这项需要写 Python 代码**，用法、示例和 Checklist 见
[配置参考的自定义路由段](docs/配置参考.md#挂-agent-自己的自定义路由register_routes)。

---

## 它替你做了什么

一条消息进来（来自 `/chat` 接口或企微），框架按顺序处理：解析并校验身份 →
空消息/重置词/hook 拦截 → 拉取白名单内的 MCP 工具 → LLM function calling 循环 →
回复 → 存上下文。

具体每一步见[配置参考的流程说明](docs/配置参考.md#框架的处理流程)。

已经内置、你不用操心的部分：

- **多轮上下文**：按会话隔离可配，超轮次自动截断，空闲过期回收
- **身份防伪造**：`identity.inject` 用可信身份覆盖模型编造的工号，
  并同步回上下文，模型不会照着被否掉的记录回答用户
- **工具白名单**：服务端有几十个工具时，只把这个 Agent 用得到的那几个交给模型
- **连接复用**：一轮对话内 MCP 只握手一次
- **多 MCP 服务端**：工具自动路由，一个挂了不影响其他
- **进度提示**：工具调用前按工具名推对应文案（企微场景）
- **异常兜底**：工具失败、模型编造工具名、LLM 挂掉都不会让 Agent 装死
- **报错可定位**：anyio TaskGroup 的异常组会拆开打，不再是一句
  `unhandled errors in a TaskGroup`
- **两个 LLM 后端**：默认 openai 官方 SDK；遇到不规范的兼容端点切
  `aiohttp` 裸 HTTP，能拿到原始响应排查
- **对外 chat 接口**：一条 `POST /chat` 把 Agent 暴露给 Dify / 内部门户 / 任何第三方
- **自定义 HTTP 端点**：某个 Agent 需要 `/chat` 之外的专属端点时，通过 `register_routes`
  扩展点挂上，不改骨架。**这项需要写 Python 代码**（区别于纯配置就能改的对话/工具/文案），
  详见[配置参考](docs/配置参考.md#挂-agent-自己的自定义路由register_routes)
- **企业微信接入**：开箱即用的长连接方案，群里 @ 即可对话，和对外接口可并存
- **技能(skill)**：把业务说明/术语/FAQ 写成 `bots/_skills/*.md`，多个 Agent 勾选引用，
  启用后拼进系统提示词
- **配置导入/导出**：一个 Agent 的配置可导出成 JSON（自动脱敏、不含密钥）、导入建新 Agent，
  方便在环境间搬运
- **密钥集中管理**：LLM 地址/密钥/模型、MCP 地址与鉴权头、chat 接口 token 都在页面明文填，
  自动落 `.env`（不进 git），`bot.yaml` 里只留 `${VAR}` 占位符

---

## 对外接口详解（对接 Dify / 门户 / 第三方）

在配置页「对外接口」页打开开关、填端口和 token（也可留空不鉴权），
或直接在 `bot.yaml` 配 `api` 段。

- **两种启动**：`serve-api`（只起接口、不连企微，这种 Agent 的企微 bot_id/secret 可以不填）
  或 `run`（连企微时一并起接口）。
- 接口无状态、只对话、不读写密钥，绑 `0.0.0.0` 内网可达。

**对接 Dify**：在 Dify 里用「自定义工具 / HTTP 请求」节点指向这个 `/chat`，
把 Dify 的当前用户传给 `user`、conversation_id 传给 `session_id`。

> 后续的门户方向规划（一进程多 Agent、流式、聚合门户页）见 [TODO.md](TODO.md)。

---

## 企业微信接入（可选）

除了对外接口，Agent 还能接入企业微信：从企微管理后台拿到 `bot_id` / `secret` 填进配置，
用 `run` 启动即可在群里 @ 它对话。企微和对外接口可以同时开，也可以只用其中一个。
详见[配置参考的 wecom 段](docs/配置参考.md#wecom-企微凭证)。

---

## 目录结构

```
bot-demo/
├── start.bat                 # Windows 双击打开配置页面
├── start-api.bat             # Windows 双击只起对外 chat 接口（不连企微）
├── start-ui.sh               # macOS / Linux 配置页面
├── start_ui.py               # 上面脚本共用的启动逻辑
├── README.md
├── README.en.md              # English version
├── TODO.md                   # 门户方向的后续规划
├── docs/
│   ├── 新建Bot步骤说明书.md
│   └── 配置参考.md
├── requirements.txt          # 依赖已钉版本
├── pytest.ini
├── botkit/                   # 框架，同事不需要改
│   ├── cli.py                # ui / new / validate / run / serve-api
│   ├── config.py             # 配置加载与校验
│   ├── yaml_writer.py        # 生成带注释的 bot.yaml（页面存盘用）
│   ├── engine.py             # function calling 循环、身份注入
│   ├── llm/                  # 两个 LLM 后端
│   ├── mcp_client.py         # MCP 连接、工具过滤、连接复用
│   ├── session.py            # 多轮上下文
│   ├── hooks.py              # 扩展点加载
│   ├── runtime.py            # 消息流程 + 企微长连接 + chat 接口并存
│   ├── api_server.py         # 对外 chat HTTP 接口（POST /chat, GET /health）
│   ├── scaffold.py           # 骨架生成
│   ├── logging_setup.py
│   ├── ui/                   # 可视化配置页面
│   │   ├── service.py        # 读写配置、探测、导入导出、技能（纯逻辑）
│   │   ├── server.py         # aiohttp 路由，只监听本机
│   │   └── static/           # 单页 HTML/CSS/JS，无构建步骤
│   └── templates/            # 脚手架模板
├── bots/                     # 你建的 Agent 放这儿，一个一个子目录
│   └── _skills/              # 共享技能文档（*.md），多个 Agent 可勾选引用
├── tools/
│   ├── wecom_probe.py        # 探企微给的用户身份（命令行版）
│   └── mcp_probe.py          # 探 MCP 工具列表与连通性（命令行版）
└── tests/                    # 单测
```

`bots/` 初始是空的 —— 示例 Agent 不随仓库分发，自己用配置页面建一个就行。

一进程一 Agent，各自独立部署、独立重启，互不影响：

```bash
python -m botkit serve-api bots/hr-assistant
python -m botkit serve-api bots/it-ticket
```

日志前缀带 Agent 名字，汇到一起也分得清。

---

## 整体带走 / 放进新仓库

`bot-demo/` 是自包含的，没有任何指向外部的引用，**整个目录复制走就能用**。

```bash
# 复制到别处
xcopy /E /I bot-demo D:\新位置\bot-demo      REM Windows
cp -r bot-demo /path/to/new/bot-demo         # macOS / Linux

cd bot-demo
pip install -r requirements.txt
python -m pytest                             # 确认环境没问题
```

放进新 git 仓库时注意：

- `.gitignore` 已经排除了 `.env`（含真实密钥）、`__pycache__`、`.pytest_cache`
- **`bots/*/.env` 不要提交**；`bots/*/bot.yaml`、`prompt.md`、
  `bots/_skills/*.md` 要提交 —— 它们不含密钥，是别人复现你配置的依据
- 命令都要在 `bot-demo` 目录下执行（`python -m botkit` 依赖这个 cwd）

---

## 开发

```bash
python -m pytest
```

几秒钟跑完，不需要网络也不烧 token：MCP 相关的用 SDK 的 in-memory
transport（走真实协议但不联网），LLM 相关的用假后端，
配置页面用 aiohttp 自带的测试客户端起真服务。

技术前提：

- **Python 3.11+**，框架用到了 `BaseExceptionGroup`
- **MCP Python SDK 必须是 v2**（`requirements.txt` 已钉 `mcp>=2.2,<3`）。
  网上的 v1 示例代码对不上，主要差异：`streamablehttp_client` →
  `streamable_http_client`、`inputSchema` → `input_schema`、
  `isError` → `is_error`、`httpx` → `httpx2`

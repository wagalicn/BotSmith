# BotSmith 待办概览

详细的评估、阶段范围、依赖和工时在本地计划文档《BotSmith 框架技术评估与迭代计划》中维护，该文档不随仓库提交。

本文件只保留当前状态，避免与实施计划重复并再次失同步。

## 已完成

- YAML + prompt 驱动的 Agent 配置；
- 企业微信长连接；
- 单 Agent `POST /chat`；
- 多 Agent 网关：`/{agent}/chat`、`GET /agents`；
- 普通 JSON 与 SSE 流式回复；
- OpenAI、OpenAI 兼容裸 HTTP、Anthropic Messages 三种后端；
- MCP 白名单、黑名单、多服务端路由和单轮连接复用；
- SessionStore 接口和内存实现；
- skills、导入导出、配置 UI 和对话预览；
- 中英文配置 UI 和主要用户错误文案；
- 用量记录；
- 门户认证原语：UserStore、PasswordAuthProvider、TokenSigner。

## 当前阶段

第一阶段安全门槛的代码、文档和回归测试已完成，完整测试 780 项通过：

- API token 强制；
- 工具前置 hook fail-closed；
- 可信身份最终覆盖；
- 身份正则全量匹配；
- 多 Agent `.env` 隔离；
- 内置与自定义路由统一鉴权；
- HTTP 和工具参数输入上限。

遗留项：网关 `GET /agents` 免鉴权且返回配置错误原文；配置页与运行时对 `.env` 引号的解析不同。下一步是阶段 2：工程保护基线。

## 后续阶段

1. 工程保护基线：`pyproject.toml`、CI、coverage、mypy；
2. 文档、测试与会话并发正确性；
3. config 模块化、参数验证、hook 超时、结构化日志；
4. 异步 SessionStore、Redis、LLM 并发控制和 readiness；
5. 独立门户 HTTP 后端、门户前端和正式发布。

## 关键边界

- `/chat` 由调用系统提供会话标识，服务端仍会保存进程内对话历史，不能称为完全无状态；
- API body 中的 `user` 是调用方声明值。门户接入时必须由服务端用已认证身份覆盖；
- `botkit/ui/` 是能读写密钥的本机配置工具，不是公网门户；
- 当前 `botkit/portal/` 只有认证原语，还没有登录 HTTP 层或终端用户页面；
- 多实例部署需要 Redis 或会话亲和；
- 发布、tag 和部署操作需要单独确认。

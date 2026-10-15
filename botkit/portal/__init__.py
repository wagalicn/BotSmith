"""
botkit 门户（ITbot 大全）

在多 Agent 网关之上叠一层面向最终用户的门户：登录、Agent 卡片列表、聊天窗。

分层约定（别推翻）：
- 门户负责「有状态」的部分：登录鉴权、登录态、为会话发 session_id。
- 对外 /chat 内核保持无状态（user / session_id 由门户提供），这是水平扩展
  和对接 Dify 的根基。
- 鉴权做成可插拔的 AuthProvider：内置账号密码，将来可加 SSO / LDAP / OIDC，
  实现同一接口即可，门户其余部分不用改。
"""

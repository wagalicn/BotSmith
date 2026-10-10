# botkit —— A Quick-Build Skeleton for API-based Agents

<p align="right"><a href="README.md">中文</a> | <a href="README.en.md">English</a></p>

Build an Agent through configuration and use it externally over an HTTP API.
Business customization lives in just two files, with **no need to touch Python code**:

- `bot.yaml` —— LLM, MCP tools, identity rules, session policy, message copy, external API
- `prompt.md` —— who this Agent is and what it should do

An Agent you build can:

- **Be exposed as an HTTP API** (`POST /chat`) for Dify, an in-house portal, or any third party —— this is the primary use
- **Connect to WeCom (WeChat Work)** via a long connection, so you can @ it in a group —— another way to plug in
- Enable **both at once**, or just one of them

**Visual configuration** (recommended, especially for colleagues not comfortable with the command line):

```
Windows   double-click start.bat
Others    ./start-ui.sh
```

Your browser opens a configuration page accessible only from the local machine: create/delete Agents, fill in keys,
**pick from the real tool list**, bind identities, write prompts (with live preview and structure checks),
attach skill docs, **open the external API**, import/export config, validate in real time, and even
**chat with the Agent right in the page to preview behavior** (independent of any external channel).

**Command line**:

```bash
python -m botkit ui                            # open the configuration page
python -m botkit new hr-assistant              # generate a skeleton
python -m botkit validate bots/hr-assistant    # validate config (secrets auto-masked)
python -m botkit serve-api bots/hr-assistant   # serve only the external chat API (Dify/portal, no WeCom)
python -m botkit run bots/hr-assistant         # connect to WeCom; also serves /chat if the external API is enabled
```

The two approaches can be mixed —— saving from the page preserves comments in `bot.yaml`,
and files edited by hand still open fine in the page.

---

## Docs

| What you want | Where to go |
|---|---|
| **Building an Agent for the first time** | [Guide: Creating a New Agent](docs/creating-a-new-agent.md) —— 10 steps, mainly via the config page, each with expected results and troubleshooting |
| **How to configure a specific field** | [Configuration Reference](docs/configuration-reference.md) —— all `bot.yaml` fields, hooks, dual backends, processing pipeline |
| What this project is about | This file |

---

## Quick start: expose an external API

The most common use —— expose the Agent as an HTTP API without WeCom:

```bash
python -m botkit new my-agent                  # generate a skeleton
# in the config page, fill in LLM/MCP keys, pick tools, write the prompt, turn on the switch in "External API"
python -m botkit serve-api bots/my-agent       # serve the API only (Windows: double-click start-api.bat)
```

API contract:

```
POST http://<server IP>:<port>/chat
Header:  Authorization: Bearer <token>     # only needed if a token is configured
Body:    {"message": "what the user said", "user": "L220104", "session_id": "conv-1"}
Resp:    {"reply": "Agent reply", "kind": "llm", "session_id": "conv-1"}

GET  http://<server IP>:<port>/health         # liveness check, no auth
```

- **`user` / `session_id` are provided by the caller**: the portal/Dify passes the current user and session ID,
  and the Agent passes them through statelessly. Consecutive requests with the same `session_id` = continuous multi-turn; without it, isolation is by `user`.
- The API binds to `0.0.0.0` (reachable on the intranet), only chats and never reads/writes secrets, a different trade-off from the "config page" (bound to localhost only).

See the [api section in the Configuration Reference](docs/configuration-reference.md#api--external-api) for fields and Dify integration details.

**Need a dedicated endpoint beyond `/chat`?** An Agent can implement `register_routes` in its own `hooks.py`
to attach custom HTTP routes (structured analysis, bulk import, etc.) to the same service, without changing the skeleton.
**This one requires Python code**; for usage, examples, and a checklist see the
[custom routes section in the Configuration Reference](docs/configuration-reference.md#attaching-an-agents-own-custom-routes-register_routes).

---

## What it does for you

When a message comes in (from the `/chat` API or WeCom), the framework processes it in order: resolve and validate identity →
intercept empty messages/reset words/hooks → fetch whitelisted MCP tools → LLM function-calling loop →
reply → store context.

See the [pipeline description in the Configuration Reference](docs/configuration-reference.md#the-frameworks-processing-pipeline) for each step.

Built in, so you don't have to worry about it:

- **Multi-turn context**: isolation by session is configurable, auto-truncation beyond the turn limit, idle expiration and recycling
- **Anti-spoofing identity**: `identity.inject` overrides any employee ID the model fabricates with a trusted identity,
  and syncs it back to the context, so the model won't answer users based on rejected records
- **Tool whitelist**: when the server has dozens of tools, only hand the model the few this Agent actually needs
- **Connection reuse**: MCP handshakes only once within a conversation turn
- **Multiple MCP servers**: tools are routed automatically; one going down doesn't affect the others
- **Progress hints**: before a tool call, push the message copy mapped to the tool name (WeCom scenario)
- **Failure fallbacks**: tool failures, fabricated tool names, or a dead LLM won't make the Agent freeze up
- **Locatable errors**: anyio TaskGroup exception groups are unpacked and logged individually, no more a single
  `unhandled errors in a TaskGroup`
- **Two LLM backends**: defaults to the official openai SDK; for non-standard compatible endpoints, switch to
  `aiohttp` raw HTTP to get the raw response for debugging
- **External chat API**: a single `POST /chat` exposes the Agent to Dify / an internal portal / any third party
- **Custom HTTP endpoints**: when an Agent needs a dedicated endpoint beyond `/chat`, attach it via the `register_routes`
  extension point without changing the skeleton. **This one requires Python code** (unlike the dialog/tools/copy that are pure config),
  see the [Configuration Reference](docs/configuration-reference.md#attaching-an-agents-own-custom-routes-register_routes)
- **WeCom integration**: an out-of-the-box long-connection option, @ it in a group to chat, can coexist with the external API
- **Skills**: write business descriptions/terminology/FAQs as `bots/_skills/*.md`, let multiple Agents opt in,
  and once enabled they are spliced into the system prompt
- **Config import/export**: an Agent's config can be exported to JSON (auto-redacted, no secrets) and imported to create a new Agent,
  making it easy to move between environments
- **Centralized secrets**: LLM address/key/model, MCP address and auth headers, chat API token are all filled in plaintext on the page,
  automatically written to `.env` (not in git), with only `${VAR}` placeholders left in `bot.yaml`

---

## External API in detail (integrating with Dify / portal / third parties)

On the config page's "External API" tab, turn on the switch and fill in the port and token (or leave blank for no auth),
or configure the `api` section directly in `bot.yaml`.

- **Two ways to start**: `serve-api` (API only, no WeCom, in which case the Agent's WeCom bot_id/secret can be left blank)
  or `run` (serves the API alongside WeCom).
- The API is stateless, only chats, never reads/writes secrets, and binds to `0.0.0.0` reachable on the intranet.

**Integrating with Dify**: in Dify, use a "custom tool / HTTP request" node pointing at this `/chat`,
passing Dify's current user as `user` and the conversation_id as `session_id`.

> For the follow-up portal roadmap (multiple Agents in one process, streaming, an aggregated portal page) see [TODO.md](TODO.md).

---

## WeCom integration (optional)

Beyond the external API, an Agent can connect to WeCom: fill the `bot_id` / `secret` from the WeCom admin console into the config,
start with `run`, and you can @ it in a group to chat. WeCom and the external API can be enabled at the same time, or you can use just one.
See the [wecom section in the Configuration Reference](docs/configuration-reference.md#wecom--wecom-credentials).

---

## Directory structure

```
bot-demo/
├── start.bat                 # Windows: double-click to open the config page
├── start-api.bat             # Windows: double-click to serve only the external chat API (no WeCom)
├── start-ui.sh               # macOS / Linux config page
├── start_ui.py               # shared startup logic for the scripts above
├── README.md
├── README.en.md              # English version
├── TODO.md                   # follow-up portal roadmap
├── docs/
│   ├── 新建Bot步骤说明书.md
│   └── 配置参考.md
├── requirements.txt          # dependencies pinned to versions
├── pytest.ini
├── botkit/                   # the framework, colleagues don't need to touch it
│   ├── cli.py                # ui / new / validate / run / serve-api
│   ├── config.py             # config loading and validation
│   ├── yaml_writer.py        # generate commented bot.yaml (used when saving from the page)
│   ├── engine.py             # function-calling loop, identity injection
│   ├── llm/                  # the two LLM backends
│   ├── mcp_client.py         # MCP connections, tool filtering, connection reuse
│   ├── session.py            # multi-turn context
│   ├── hooks.py              # extension point loading
│   ├── runtime.py            # message pipeline + WeCom long connection + chat API coexistence
│   ├── api_server.py         # external chat HTTP API (POST /chat, GET /health)
│   ├── scaffold.py           # skeleton generation
│   ├── logging_setup.py
│   ├── ui/                   # the visual config page
│   │   ├── service.py        # read/write config, probing, import/export, skills (pure logic)
│   │   ├── server.py         # aiohttp routes, listens on localhost only
│   │   └── static/           # single-page HTML/CSS/JS, no build step
│   └── templates/            # scaffold templates
├── bots/                     # the Agents you build go here, one subdirectory each
│   └── _skills/              # shared skill docs (*.md), multiple Agents can opt in
├── tools/
│   ├── wecom_probe.py        # probe the user identity WeCom provides (CLI version)
│   └── mcp_probe.py          # probe the MCP tool list and connectivity (CLI version)
└── tests/                    # unit tests
```

`bots/` is empty initially —— sample Agents are not shipped with the repo; just build one with the config page.

One process, one Agent, each deployed and restarted independently without affecting the others:

```bash
python -m botkit serve-api bots/hr-assistant
python -m botkit serve-api bots/it-ticket
```

Log prefixes carry the Agent name, so they stay distinguishable even when aggregated.

---

## Taking it whole / dropping it into a new repo

`bot-demo/` is self-contained with no references pointing outside, **just copy the whole directory and it works**.

```bash
# copy elsewhere
xcopy /E /I bot-demo D:\new-location\bot-demo   REM Windows
cp -r bot-demo /path/to/new/bot-demo            # macOS / Linux

cd bot-demo
pip install -r requirements.txt
python -m pytest                                # confirm the environment is fine
```

When dropping it into a new git repo, note:

- `.gitignore` already excludes `.env` (contains real secrets), `__pycache__`, `.pytest_cache`
- **Do not commit `bots/*/.env`**; do commit `bots/*/bot.yaml`, `prompt.md`,
  `bots/_skills/*.md` —— they contain no secrets and are what others rely on to reproduce your config
- Run all commands inside the `bot-demo` directory (`python -m botkit` depends on this cwd)

---

## Development

```bash
python -m pytest
```

Runs in a few seconds, needs no network and burns no tokens: MCP-related tests use the SDK's in-memory
transport (real protocol, no network), LLM-related tests use a fake backend,
and the config page uses aiohttp's built-in test client to spin up a real service.

Technical prerequisites:

- **Python 3.11+**, the framework uses `BaseExceptionGroup`
- **The MCP Python SDK must be v2** (`requirements.txt` pins `mcp>=2.2,<3`).
  v1 example code online won't match; the main differences: `streamablehttp_client` →
  `streamable_http_client`, `inputSchema` → `input_schema`,
  `isError` → `is_error`, `httpx` → `httpx2`

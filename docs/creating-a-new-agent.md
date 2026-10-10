# Guide: Creating a New Agent

<p align="right"><a href="新建Bot步骤说明书.md">中文</a> | <a href="creating-a-new-agent.md">English</a></p>

Follow along and you'll build a working WeCom (WeChat Work) Agent. **No Python code needed the whole way.**

The main path uses the visual config page (just point and click). Under each step there's a collapsible
"Don't want to use the page?" with the equivalent command-line approach.

- Estimated time: 40 minutes (most of it spent writing the prompt)
- You need to be able to: fill in forms, copy and paste
- You don't need to know: Python, YAML, the MCP protocol

For the detailed meaning of each field, see the [Configuration Reference](configuration-reference.md).

---

## Table of contents

- [Step 0: Set up the environment](#step-0-set-up-the-environment)
- [Step 1: Get WeCom credentials](#step-1-get-wecom-credentials)
- [Step 2: Open the config page](#step-2-open-the-config-page)
- [Step 3: Create an Agent and fill in keys](#step-3-create-an-agent-and-fill-in-keys)
- [Step 4: Confirm it can recognize who the user is](#step-4-confirm-it-can-recognize-who-the-user-is)
- [Step 5: Pick tools](#step-5-pick-tools)
- [Step 6: Bind identity (security critical)](#step-6-bind-identity-security-critical)
- [Step 7: Write the prompt](#step-7-write-the-prompt)
- [Step 8: Review the config summary](#step-8-review-the-config-summary)
- [Step 9: Preview and test online](#step-9-preview-and-test-online)
- [Step 10: Start it and verify in a group](#step-10-start-it-and-verify-in-a-group)
- [Step 11: Go-live checklist](#step-11-go-live-checklist)
- [Stuck? Look here](#stuck-look-here)

---

## Step 0: Set up the environment

Requires Python 3.11 or higher.

```bash
python --version
```

Expected: `Python 3.11.x` or higher. Below 3.11, upgrade first (the framework uses
`BaseExceptionGroup`, which errors as a syntax error on 3.10 and below).

Enter the project directory and install dependencies:

```bash
cd bot-demo
pip install -r requirements.txt
```

> **You can skip this on Windows** —— double-click `start.bat`,
> it checks Python and dependencies itself and installs what's missing.

<details>
<summary>What if installation fails</summary>

- `python` is not a command —— Python isn't installed, or "Add Python to PATH" wasn't checked during install.
  Reinstall and be sure to check that option.
- Company network can't install packages —— ask a colleague for an internal pip mirror:
  `pip install -i <internal mirror URL> -r requirements.txt`
</details>

---

## Step 1: Get WeCom credentials

Go to the WeCom admin console: **Smart Robot → New Robot → enter the robot → API mode → long connection**.

Note down two values:

- **bot_id**
- **secret**

> **Have a super admin create the Agent.** An Agent created by a super admin receives plaintext employee IDs
> (like `L220104`) with incoming messages; otherwise you get an encrypted string the business system can't
> recognize, and you'll need extra mapping code later. Step 4 verifies this on the spot.

Also confirm two more things:

- **The MCP server address** —— ask the platform owner
- **The LLM endpoint and key** —— ask the AI platform owner

---

## Step 2: Open the config page

**Windows**: double-click `start.bat` in the project directory.

**macOS / Linux**:

```bash
./start-ui.sh
```

The browser opens `http://127.0.0.1:8771/` automatically. Don't close the command-line window,
closing it stops the page.

> This page is only accessible from your computer and is never exposed to the network ——
> it can read/write real secrets, so it's intentionally designed this way.

<details>
<summary>Don't want to use the page?</summary>

```bash
python -m botkit ui --no-browser    # start the service without opening a browser
python -m botkit ui --port 8888      # change the port
python -m botkit --help              # see all commands
```

Every later step can be done purely with the command line + an editor; each step's collapsible block has the approach.
</details>

<details>
<summary>What if the page won't open</summary>

- **Port in use** (default 8771) —— the command line prompts you, change it: `python -m botkit ui --port 8888`
- **Browser didn't open automatically** —— manually visit the address printed in the command line
- **Blank page** —— press F12 to open the console for errors, and send them to whoever maintains this framework
</details>

---

## Step 3: Create an Agent and fill in keys

1. Click **"New"** at the top left and enter a name (e.g. `hr-assistant`).
   Starts with a letter; only letters, digits, underscores, and hyphens.
2. After creation it jumps to the **"Keys"** page. The entries listed there are computed automatically from the config;
   fill in the values you got in Step 1.

When done, click **"Save"** at the bottom right (or press `Ctrl+S`).

**`LLM_API_URL` is the most error-prone**: by default it uses the official SDK, so the address must go
**up to `/v1`, without `/chat/completions`** —— the SDK appends it. If you get it wrong, saving tells you how to fix it.

The dot before the Agent name on the left turns from **red to green** —— green means the config passes validation.

<details>
<summary>Don't want to use the page?</summary>

```bash
python -m botkit new hr-assistant
copy bots\hr-assistant\.env.example bots\hr-assistant\.env    REM Windows
cp bots/hr-assistant/.env.example bots/hr-assistant/.env      # macOS/Linux
```

Then edit `bots/hr-assistant/.env` to fill in the 6 values, and run `python -m botkit validate bots/hr-assistant`.
</details>

---

## Step 4: Confirm it can recognize who the user is

**This is the easiest step to get stuck on, so do it early.**

The Agent needs to know "who is speaking right now" to file tickets or submit requests on that person's behalf.
The user ID WeCom provides may be a plaintext employee ID, or an encrypted string.

On the "Keys" page, scroll down to a **"Verify WeCom connection"** area:

1. First click **"Test credentials"** —— it should show "WeCom credentials correct".
2. Then click **"Wait for me to @ a line in WeCom"**, switch to WeCom, and
   **@ your Agent** with a message (e.g. "test").

The page will show:

| Item | What you want to see |
|---|---|
| Recognized user ID | a plaintext employee ID like `L220104` |
| Looks like an encrypted string? | no, it's plaintext |
| Identity validation result | passed (green) |

A green "identity validation passed" means you can move to the next step.

<details>
<summary>"Test credentials" fails: 853000 invalid bot_id or secret</summary>

The bot_id or secret in `.env` is wrong. Go back to Step 1 and copy again, being careful not to include leading/trailing spaces.
</details>

<details>
<summary>Clicked "Wait for me to @ a line" but nothing happens</summary>

- Confirm the Agent has been added to that group, or you're in a 1:1 with it
- In a group you must **@ it**; it doesn't receive plain messages
- Confirm the Agent in the WeCom console is "API mode / long connection", not "callback mode"
</details>

<details>
<summary>Identity validation fails, but the ID looks like a plaintext employee ID</summary>

The format check is too strict. Go to the **"Identity & Security"** page and change "employee ID format check" to a
suitable option, or pick "don't validate" for now. Save and come back to test again.
</details>

<details>
<summary>Identity validation fails, the ID is an encrypted string like wo1234xxxx==</summary>

The business system can't recognize this ID; there are two paths:

1. **Recommended**: have a super admin recreate the Agent, which usually yields a plaintext employee ID. Back to Step 1.
2. Do the mapping yourself: edit `bots/<your-bot>/hooks.py`, uncomment the
   `resolve_identity` block, and write "encrypted ID → employee ID" lookup logic;
   then change the identity source on the "Identity & Security" page to "custom (hooks.py)".
   This requires you to have a mapping (usually obtained via a WeCom self-built app).
</details>

<details>
<summary>Don't want to use the page?</summary>

```bash
python tools/wecom_probe.py bots/hr-assistant
```

Then @ the Agent in WeCom with a message and watch the console output. `Ctrl+C` to exit.
</details>

---

## Step 5: Pick tools

An Agent's capabilities come from the tools the MCP server provides.

Switch to the **"Tools"** page; at the top is the **"MCP servers"** management area. There's one server by default.
**If this Agent needs to connect to multiple platforms** (e.g. IT tickets and CRM at once), click
**"+ Add a server"**: each gets an identifier (your choice, like `oa`, `crm`, must be unique),
an address (fill the plaintext URL directly in each server's **"address"** box, e.g.
`https://mcp.example.com/oa`; on save it's stored in the local `.env`, not in git,
and not shown on the "Keys" page), and an identity pass-through header. Tools from multiple servers are merged for the model.

After configuring the servers, click **"Probe all servers' tools"** (or
**"Probe just this one"** next to a server). The page connects and lists the tools each server
actually provides as checkboxes, each with a description and parameters.

**Check the ones this Agent truly needs, leave the rest unchecked.**

> Don't be lazy and check all or leave it blank. More tools make the model more likely to pick wrong, and each request
> carries a pile of parameter descriptions wasting tokens. A typical example: the platform provides 26 tools,
> while a ticket-focused Agent needs only 2 of them.

Save when done.

<details>
<summary>Probing reports "config hasn't passed validation"</summary>

Go back to Step 3 to fill in and save all keys, and probe once the left dot turns green.
</details>

<details>
<summary>Probing reports it can't connect (getaddrinfo failed / connection attempts failed)</summary>

- `getaddrinfo failed` —— DNS can't resolve. Check the MCP address for typos,
  and whether your machine can reach that address (may need the intranet/VPN).
- `All connection attempts failed` —— the address resolves but can't connect.
  Confirm the port is right and the server is running.
</details>

<details>
<summary>Tools are listed, but calls return "this operation requires login first"</summary>

The server requires a browser login, but a group chat has no browser environment. The server needs a no-login
policy for the Agent source —— it recognizes the request header `X-MCP-Identity`
or the employee ID in the parameters to allow access.

**This is not something you can solve on the Agent side.** Find the platform owner and tell them:
you already carry `X-MCP-Identity: <employee ID>` on every request, and you need them to allow this source
for the specified tools. Once configured, nothing changes on the Agent side.
</details>

<details>
<summary>Don't want to use the page?</summary>

```bash
python tools/mcp_probe.py bots/hr-assistant                       # list all tools
python tools/mcp_probe.py bots/hr-assistant --call get_xxx        # try calling one
```

Then manually fill the tool names into `bot.yaml`'s `mcp.servers[].allow_tools`.
</details>

---

## Step 6: Bind identity (security critical)

Switch to the **"Identity & Security"** page and look at the yellow
**"Prevent the model from acting as someone else"** area in the lower half.

**Why it's a must**: the model makes up arguments on its own. If a tool has a "who submitted this" kind of parameter
(a ticket's submitter, an expense's applicant, a leave's employee ID), a casual
"file a fault report for Zhang San" from the user could make the model fill the submitter as Zhang San.

Click **"Add a binding"** and choose from the dropdowns:

```
the parameter [work_code] of [create_it_ticket] = the current user
```

The options in both dropdowns come from the real tools and parameters probed in Step 5,
so you can't fill in a wrong name. The page also guesses which parameter is the "actor", usually no change needed.

**Add one for every "act on someone's behalf" tool.** Save when done.

This is a code-level guarantee: the framework overwrites the model's value with the trusted identity obtained on the
WeCom side before the call is actually made. Far more reliable than writing "don't fabricate employee IDs" in the prompt ——
prompts can be bypassed.

---

## Step 7: Write the prompt

Switch to the **"Prompt"** page.

**This step decides whether the Agent is good to use, more than tuning any parameter.** Worth spending extra time.

Write it in four sections:

```markdown
# Role
You are the HR assistant, running in WeCom, helping colleagues with leave, expenses, and attendance queries.

# Core task
Colleagues describe in natural language what they want to do; you decide which tool to call,
complete the information, submit, and tell them the result.

# Workflow
1. First call get_leave_types to get the list of leave types.
2. Based on the colleague's description, match a leave_type_id from the returned result.
3. Confirm the start time, end time, and reason are all present, then call submit_leave.
   - The employee ID is provided by the system, no need to ask.
   - Never fabricate a leave_type_id; it must come from step 1's return.

# Answer requirements
1. Answer concisely in English.
2. When information is incomplete, ask proactively, don't guess.
3. On a successful submission, clearly report the ticket number and status.
4. For off-topic chit-chat, respond briefly and steer back to the point.
5. Don't show internal IDs to the user.
```

A few practical tips:

- **You don't write how to use the tools**; the model sees each tool's own description and parameters.
  Here, focus on **what order to call in** and **what not to do**.
- **Use hard words for constraints.** "Never" / "must" work far better than "suggest" / "try to".
- **If the model keeps skipping a step, pull that step out into its own numbered rule**,
  don't bury it in a paragraph.
- **Add a line "provided by the system" for identity parameters.** Even though Step 6 guarantees it at the code level,
  telling the model saves it an extra question.

---

## Step 8: Review the config summary

Switch to the **"Other"** page and look at the **"Current config summary"** at the bottom (keys are masked).

**Review each item**, especially these:

- Does `api_url` go up to `/v1`
- Does the **whitelist** contain the tools you want
- Is the **force-inject** line present —— if empty, back to Step 6
- Can the **validation regex** match the real employee ID you saw in Step 4

Also take a look at the "code extension points" block; normally it should show
"all extension points are commented out, currently not in effect".

<details>
<summary>Don't want to use the page?</summary>

```bash
python -m botkit validate bots/hr-assistant
```
</details>

---

## Step 9: Preview and test online

**Without connecting to WeCom, chat with the Agent in the page first to see how it does.** This step verifies the prompt,
tools, and identity injection together before going live, far faster than connecting to WeCom to try.

Switch to the **"Preview test"** page, send a line a real user would say in the input box
(e.g. "I'd like to take three days of annual leave"), and press Enter.

The page shows:
- which tools the Agent called (expand to see the actual parameters and returns)
- the final reply

Check three things against the result:

1. **Did it pick the right tools** —— called what it should, didn't call what it shouldn't
2. **Did identity injection take effect** —— expand the tool call and see whether parameters like "submitter/applicant"
   are the one you filled in "which identity to test with", not what the model made up
3. **Reply quality** —— whether the answer is correct and whether it missed asking for key information

If unsatisfied, go back to Step 7 to change the prompt, save, and send another line, with immediate effect.

> **Preview actually executes tools.** It goes through exactly the same flow as production,
> so if the prompt triggers an action like "create a ticket", it **actually creates one** here.
> Use test data, not real business information.
</details>

<details>
<summary>What if the preview errors</summary>

The preview actually connects to the LLM and MCP, so:

- **"Config hasn't passed validation"** —— go back to Step 3/8 to get the config right.
- **"Failed to call the model"** —— the LLM address or key is wrong, back to Step 3 to check.
- **"Failed to call the MCP tool"** —— the tool server is unreachable or requires login, back to Step 5.
- **"Identity doesn't match identity.pattern"** —— fill in an employee ID matching the format in
  "which identity to test with", or go back to Step 6 to adjust the validation rule.

None of these are bugs; the preview only matters because it really connects.
</details>

<details>
<summary>Don't want to use the page?</summary>

There's no equivalent command-line preview. Either use the page, or go straight to Step 10 to test on WeCom.
</details>

---

## Step 10: Start it and verify in a group

The config page only handles config; **starting the Agent uses the command line** (it's a long-running service,
better managed with your existing ops practices).

Open a new command-line window:

```bash
cd bot-demo
python -m botkit run bots/hr-assistant
```

Expected output:

```
2026-09-17 13:19:41 [hr-assistant] INFO  [botkit.runtime] Starting Agent hr-assistant
2026-09-17 13:19:42 [hr-assistant] INFO  [botkit.runtime] LLM backend : openai (... / qwen3.7-plus)
2026-09-17 13:19:42 [hr-assistant] INFO  [botkit.runtime] MCP server  : main ... tools=get_leave_types, submit_leave
2026-09-17 13:19:42 [hr-assistant] INFO  [botkit.runtime] WebSocket connected
2026-09-17 13:19:43 [hr-assistant] INFO  [botkit.runtime] Authenticated, Agent hr-assistant is online
```

Seeing **`is online`** means it's connected. Keep the window open and @ it in the WeCom group.

### Verify at least these 5

| Input | Expected |
|---|---|
| `@Agent I'd like three days of annual leave` | goes through the flow, returns a ticket number |
| `@Agent` (just @, no words) | replies the "just @'d, no words" copy you configured |
| `@Agent restart` | replies the "reset done" copy, context cleared |
| two consecutive related lines | the second picks up the first's context |
| `@Agent file leave for Zhang San` | **the applicant is still you** |

The last one matters most —— it verifies the binding from Step 6 actually took effect. After the run, check whether this line is in the logs:

```
WARNING [botkit.engine] the LLM passed 'xxx' for submit_leave's employee_code, overwritten with trusted identity 'L220104'
```

This line means anti-spoofing is working.

<details>
<summary>Reply "Sorry, an error occurred while processing"</summary>

Look at the run window's logs, scroll up to the ERROR lines; the real reason is there.
Setting the log level to `DEBUG` on the config page's "Other" page shows more detail.
</details>

<details>
<summary>Reply "Can't recognize your employee ID"</summary>

Go back to Step 4.
</details>

<details>
<summary>Reply "Processing timed out (too many tool rounds)"</summary>

The model is looping on tool calls. Check the logs for which one it loops on, then go back to Step 7 and
write that step's constraint as its own numbered rule. Raising the round limit doesn't fix the root cause.
</details>

<details>
<summary>Wrong answers, skipped steps, fabricated IDs</summary>

A prompt problem, back to Step 7. Write the failing constraint harder and more specific.
Save and restart the Agent to take effect.
</details>

---

## Step 11: Go-live checklist

Before handing it to colleagues for daily use, run through:

- [ ] The left dot on the config page is green
- [ ] "Force inject" is configured for every "act on someone's behalf" tool
- [ ] The tool whitelist only checks what's truly needed
- [ ] The employee ID format check matches the real employee ID
- [ ] Tried a round in "Preview test" with tools and identity injection both correct (Step 9)
- [ ] Verified the 5 items from Step 10 in a group
- [ ] Tried a failing scenario (e.g. missing parameters), confirming the Agent doesn't freeze up
- [ ] `.env` is **not** committed to git
- [ ] `bot.yaml` and `prompt.md` are committed so others can reproduce your config
- [ ] Decided who restarts the Agent if it goes down (process supervisor / autostart per your ops habits)

---

## Stuck? Look here

Look up by symptom:

| Symptom | Where to go |
|---|---|
| `python` is not a command / missing dependencies | [Step 0](#step-0-set-up-the-environment) |
| config page won't open / port in use | [Step 2](#step-2-open-the-config-page) |
| `853000 invalid bot_id or secret` | [Step 4](#step-4-confirm-it-can-recognize-who-the-user-is) |
| @'d the Agent, no response | [Step 4](#step-4-confirm-it-can-recognize-who-the-user-is) |
| reply "Can't recognize your employee ID" | [Step 4](#step-4-confirm-it-can-recognize-who-the-user-is) |
| MCP can't connect / requires login | [Step 5](#step-5-pick-tools) |
| tool list is empty | [Step 5](#step-5-pick-tools) |
| config error on save | [Step 3](#step-3-create-an-agent-and-fill-in-keys) |
| preview test errors | [Step 9](#step-9-preview-and-test-online) |
| reply "an error occurred while processing" | [Step 10](#step-10-start-it-and-verify-in-a-group) |
| poor answer quality | [Step 7](#step-7-write-the-prompt) or [Step 9](#step-9-preview-and-test-online) |
| want to change a specific behavior | [Configuration Reference](configuration-reference.md) |

The two troubleshooting tools can be run on their own anytime, without affecting a running Agent:

```bash
python tools/wecom_probe.py bots/your-bot    # see what identity WeCom provides
python tools/mcp_probe.py  bots/your-bot      # see which tools you can get
```

To see exactly how the Agent runs internally, set the log level to `DEBUG`.

---

## Changing things later

Most changes are just point-and-click on the config page; save and restart the Agent to take effect.

| What you want to change | Where on the page | Corresponding file/field |
|---|---|---|
| role, wording, flow constraints | Prompt | `prompt.md` |
| add/remove usable tools | Tools | `mcp.servers[].allow_tools` |
| change model, tune timeout | Model | `llm` |
| welcome message, error prompts, progress copy | Copy | `messages` |
| how many turns to remember, when to expire | Session | `session` |
| per-person vs. whole-group isolation in groups | Session | `session.scope` |
| employee ID format, identity binding | Identity & Security | `identity` |
| log verbosity | Other | `logging.level` |
| add fixed replies like a help menu | (requires code) | `on_message` in `hooks.py` |
| add business descriptions/terminology/FAQ | Skills | `skills` (docs in `bots/_skills/`, opt in) |
| expose as an HTTP API for Dify/portal | External API | `api` (see [Configuration Reference](configuration-reference.md#api--external-api)) |
| move to another environment / copy config | title bar "Export JSON" / sidebar "Import" | export has no secrets, add keys after import |
| verify after a change | Preview test | — |
| delete an Agent | title bar "Delete this Agent" | moved to `bots/.trash/`, recoverable |

**The page and manual edits can be mixed.** Saving from the page preserves comments in `bot.yaml`,
so you can also edit the file directly in an editor, and it still opens fine in the page next time.

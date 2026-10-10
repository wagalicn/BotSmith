/*
 * botkit 配置页面
 *
 * 原生 JS，没有框架也没有构建步骤 —— 同事拿到目录就能跑。
 *
 * 核心思路：state.values 就是 bot.yaml 的内容（一个普通对象），
 * 表单控件通过 "llm.client" 这样的路径读写它，保存时整个 POST 回去。
 * 后端用带注释的生成器写文件，所以手改和页面改可以长期共存。
 */

"use strict";

const state = {
  bots: [],
  options: null,
  current: null,     // 当前 bot 名字
  values: null,      // bot.yaml 的内容
  prompt: "",
  env: [],           // [{key, value, filled, hint}]
  hooks: null,
  probed: {},        // 服务端名 -> [{name, description, params}]
  dirty: false,
  showSecrets: false,
  activeTab: "secrets",
  skillsCatalog: [],   // 共享目录里所有 skill：[{name, summary, chars, used_by}]
  skillsLoaded: false, // 技能清单拉到过没有；没拉到之前不判断引用是否失效
  preview: { history: [], turns: [], busy: false }, // 当前 Agent 的预览对话
  previews: {},      // 按 Agent 名存预览，切回来能恢复：name -> {history, turns, busy}
};

const TABS = [
  { id: "secrets", label: "密钥" },
  { id: "llm", label: "大模型" },
  { id: "tools", label: "工具" },
  { id: "identity", label: "身份与安全" },
  { id: "prompt", label: "提示词" },
  { id: "skills", label: "技能" },
  { id: "session", label: "会话" },
  { id: "messages", label: "文案" },
  { id: "intercepts", label: "前置拦截" },
  { id: "api", label: "对外接口" },
  { id: "preview", label: "预览测试" },
  { id: "advanced", label: "其他" },
];

// ---------------------------------------------------------------- 工具函数

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

/** 按 "a.b.c" 路径取值 */
function getPath(obj, path, fallback = undefined) {
  let node = obj;
  for (const key of path.split(".")) {
    if (node === null || typeof node !== "object") return fallback;
    node = node[key];
  }
  return node === undefined || node === null ? fallback : node;
}

/** 按 "a.b.c" 路径写值，中间层不存在就建 */
function setPath(obj, path, value) {
  const keys = path.split(".");
  let node = obj;
  for (const key of keys.slice(0, -1)) {
    if (typeof node[key] !== "object" || node[key] === null) node[key] = {};
    node = node[key];
  }
  node[keys.at(-1)] = value;
}

async function api(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务端返回了非 JSON 内容（HTTP ${response.status}）`);
  }
  if (!response.ok) throw new Error(payload?.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function setStatus(text, kind = "") {
  const node = $("#status");
  node.textContent = text;
  node.className = "status" + (kind ? " " + kind : "");
}

function markDirty() {
  state.dirty = true;
  setStatus("有未保存的改动", "busy");
}

function note(kind, text) {
  return el("div", { class: "note " + kind, text });
}

// ---------------------------------------------------------------- 字段构件

/**
 * 造一个输入框。path 是它在 bot.yaml 里的位置。
 */
function textField({ path, label, note: noteText, placeholder, narrow, type = "text" }) {
  const input = el("input", {
    type,
    id: "f-" + path.replace(/\./g, "-"),
    class: narrow ? "narrow" : null,
    placeholder: placeholder || "",
    value: getPath(state.values, path, "") ?? "",
    oninput: (e) => {
      let value = e.target.value;
      if (type === "number") value = value === "" ? null : Number(value);
      setPath(state.values, path, value);
      markDirty();
    },
  });
  return wrap(input, label, noteText);
}

function selectField({ path, label, note: noteText, choices, fallback }) {
  const current = getPath(state.values, path, fallback);
  const select = el("select", {
    id: "f-" + path.replace(/\./g, "-"),
    onchange: (e) => {
      setPath(state.values, path, e.target.value);
      markDirty();
      render();
    },
  });
  for (const choice of choices) {
    select.append(
      el("option", {
        value: choice.value,
        selected: String(current) === String(choice.value),
        text: choice.label,
      })
    );
  }
  const active = choices.find((c) => String(c.value) === String(current));
  return wrap(select, label, noteText, active?.note);
}

function textareaField({ path, label, note: noteText, rows = 3 }) {
  const area = el("textarea", {
    id: "f-" + path.replace(/\./g, "-"),
    rows,
    oninput: (e) => {
      setPath(state.values, path, e.target.value);
      markDirty();
    },
  });
  area.value = getPath(state.values, path, "") ?? "";
  return wrap(area, label, noteText);
}

function wrap(control, label, noteText, extraNote) {
  const field = el("div", { class: "field" });
  field.append(el("label", { class: "field-label", for: control.id, text: label }));
  if (noteText) field.append(el("p", { class: "field-note", text: noteText }));
  field.append(control);
  if (extraNote) field.append(el("p", { class: "field-note", text: extraNote }));
  return field;
}

// ---------------------------------------------------------------- 侧边栏

function renderSidebar() {
  const list = $("#bot-list");
  list.replaceChildren();

  if (!state.bots.length) {
    list.append(el("li", { class: "empty-list", text: "还没有 Agent，点「新建」" }));
    return;
  }

  for (const bot of state.bots) {
    const button = el("button", {
      type: "button",
      class: "bot-item" + (bot.name === state.current ? " active" : ""),
      onclick: () => selectBot(bot.name),
      title: bot.ok ? "配置正常" : bot.error,
    }, [
      el("span", { class: "dot " + (bot.ok ? "ok" : "bad") }),
      el("span", { text: bot.name }),
    ]);
    list.append(el("li", {}, button));
  }
}

// ---------------------------------------------------------------- 分区

function renderTabs() {
  const tabs = $("#tabs");
  tabs.replaceChildren();
  for (const tab of TABS) {
    tabs.append(
      el("button", {
        type: "button",
        class: "tab" + (tab.id === state.activeTab ? " active" : ""),
        onclick: () => {
          state.activeTab = tab.id;
          render();
        },
        text: tab.label,
      })
    );
  }
  for (const panel of $$(".panel")) {
    panel.hidden = panel.dataset.panel !== state.activeTab;
  }
}

// ---------------------------------------------------------------- 密钥

/** 没填企微 bot_id / bot_secret 时，禁用「验证企微连接」的两个按钮 */
function updateWecomTestButtons() {
  const val = (key) => {
    const item = state.env.find((e) => e.key === key);
    return item ? String(item.value || "").trim() : "";
  };
  // env 里没这两个占位符（纯对外接口 Agent）也视为不可测
  const ready = !!val("WECHAT_BOT_ID") && !!val("WECHAT_BOT_SECRET");
  for (const id of ["#btn-test-wecom", "#btn-wait-wecom"]) {
    const btn = $(id);
    if (!btn) continue;
    btn.disabled = !ready;
    btn.title = ready ? "" : "请先在上方填写 WECHAT_BOT_ID 和 WECHAT_BOT_SECRET";
  }
}

function renderSecrets() {
  const box = $("#env-fields");
  box.replaceChildren();

  if (!state.env.length) {
    box.append(el("p", { class: "empty-list", text: "这个配置没有用到 ${} 占位符。" }));
    return;
  }

  for (const item of state.env) {
    const isSecret = /SECRET|KEY|TOKEN|PASSWORD/i.test(item.key);
    const input = el("input", {
      type: isSecret && !state.showSecrets ? "password" : "text",
      id: "env-" + item.key,
      value: item.value,
      placeholder: item.hint || "",
      autocomplete: "off",
      spellcheck: "false",
      oninput: (e) => {
        item.value = e.target.value;
        item.filled = !!e.target.value.trim();
        if (item.key === "WECHAT_BOT_ID" || item.key === "WECHAT_BOT_SECRET") updateWecomTestButtons();
        markDirty();
      },
    });
    const field = wrap(input, item.key, envHint(item.key));
    if (!item.filled) {
      field.classList.add("invalid");
      field.append(el("p", { class: "field-error", text: "还没填" }));
    }
    box.append(field);
  }
}

const ENV_HINTS = {
  WECHAT_BOT_ID: "企微后台 → 智能机器人 → API模式 → 长连接",
  WECHAT_BOT_SECRET: "同上，和 bot_id 一起给出",
  LLM_API_URL: "用 openai 后端时填到 /v1 为止，不要带 /chat/completions",
  LLM_API_KEY: "大模型平台的密钥",
  LLM_MODEL: "模型名，如 qwen3.7-plus",
  // MCP 地址不在这里 —— 它在「工具」页每个服务端旁边填
};

function envHint(key) {
  return ENV_HINTS[key] || "";
}

// ---------------------------------------------------------------- 大模型

function renderLlm() {
  const box = $("#llm-fields");
  const client = getPath(state.values, "llm.client", "openai");
  box.replaceChildren(
    selectField({
      path: "llm.client",
      label: "接口方式",
      choices: state.options.llm_clients,
      fallback: "openai",
      note: "不确定就用推荐的那个",
    }),
    textField({
      path: "llm.api_url",
      label: "接口地址（api_url）",
      placeholder: "https://xxx.com/compatible-mode/v1",
      note: "直接填明文，会存进本地 .env、不进 git。openai 后端填到 /v1 为止，不要带 /chat/completions",
    }),
    textField({
      path: "llm.api_key",
      label: "接口密钥（api_key）",
      type: state.showSecrets ? "text" : "password",
      note: "大模型平台的密钥。存进本地 .env、不进 git",
    }),
    textField({
      path: "llm.model",
      label: "模型（model）",
      placeholder: "qwen3.7-plus",
      note: "模型名，直接填明文。存进本地 .env、不进 git",
    }),
    textField({
      path: "llm.timeout_seconds",
      label: "单次请求超时（秒）",
      type: "number",
      narrow: true,
      note: "带工具调用的一轮对话会请求多次，总耗时可能是它的几倍",
    }),
    textField({
      path: "llm.max_tool_rounds",
      label: "最多连续调几轮工具",
      type: "number",
      narrow: true,
      note: "防止模型反复调工具打转。到上限会回「工具轮次用尽」的文案",
    })
  );

  // 显示/隐藏 api_key 明文（复用全局 showSecrets）
  box.append(
    el("div", { class: "row" }, [
      el("button", {
        type: "button",
        class: "btn btn-ghost btn-small",
        text: state.showSecrets ? "隐藏密钥明文" : "显示密钥明文",
        onclick: () => {
          state.showSecrets = !state.showSecrets;
          render();
        },
      }),
    ])
  );

  if (client === "aiohttp") {
    box.prepend(
      note(
        "warn",
        "兼容模式下地址要填完整端点（含 /chat/completions）。" +
          "这个模式没有自动重试，一般只在官方 SDK 报 pydantic 校验错时才用。"
      )
    );
  }
}

// ---------------------------------------------------------------- 工具

function currentServers() {
  const servers = getPath(state.values, "mcp.servers", []);
  return Array.isArray(servers) ? servers : [];
}

function ensureServers() {
  let servers = getPath(state.values, "mcp.servers", null);
  if (!Array.isArray(servers)) {
    servers = [];
    setPath(state.values, "mcp.servers", servers);
  }
  return servers;
}

function renderTools() {
  renderServerList();
  renderToolPicker();
}

/** MCP 服务端管理：增删改每个服务端的 name / url / 身份头 */
function renderServerList() {
  const box = $("#server-list");
  if (!box) return;
  box.replaceChildren();

  const servers = currentServers();
  if (!servers.length) {
    box.append(
      el("p", { class: "empty-list", text: "还没有 MCP 服务端。点下面「添加」加一个。" })
    );
    return;
  }

  servers.forEach((server, index) => {
    const card = el("div", { class: "server-card" });

    const head = el("div", { class: "server-card-head" }, [
      el("strong", { text: `服务端 ${index + 1}` }),
      el("button", {
        type: "button",
        class: "btn btn-icon btn-danger",
        text: "删除",
        "aria-label": `删除服务端 ${server.name || index + 1}`,
        title: "删除这个服务端",
        onclick: () => removeServer(index),
      }),
    ]);
    card.append(head);

    card.append(
      serverField(index, "name", "标识名", "自取，如 oa、crm。用于日志和工具归属", "narrow")
    );
    card.append(
      serverField(
        index,
        "address",
        "地址",
        "MCP 服务端 URL，如 https://your-host:8081/mcp。" +
          "直接填明文即可 —— 它会存进本地 .env、不进 git。"
      )
    );
    card.append(
      serverField(
        index,
        "identity_header",
        "身份透传头",
        "把当前用户身份透传给服务端的请求头名。留空则不发。默认 X-MCP-Identity"
      )
    );
    card.append(renderServerHeaders(index));

    box.append(card);
  });
}

/** 额外固定请求头（如 Authorization: Bearer xxx）。探测和运行都会带上。
 *  值多是 token，保存时会落到本地 .env、不进 git、也不在密钥页显示。 */
function renderServerHeaders(index) {
  const wrapEl = el("div", { class: "field server-headers" });
  wrapEl.append(el("label", { class: "field-label", text: "额外请求头" }));
  wrapEl.append(
    el("p", {
      class: "field-note",
      text:
        "服务端要求鉴权时填，比如头名 Authorization、值 Bearer xxx。" +
        "值会存进本地 .env、不进 git。",
    })
  );

  const server = currentServers()[index] || {};
  const headers = server.headers && typeof server.headers === "object" ? server.headers : {};
  // 用有序数组表示，避免编辑头名时 key 变动导致光标/顺序错乱
  const entries = Array.isArray(server._headerRows)
    ? server._headerRows
    : Object.entries(headers).map(([k, v]) => ({ key: k, value: v }));

  const list = el("div", { class: "header-rows" });
  entries.forEach((row, ri) => {
    const keyInput = el("input", {
      type: "text",
      class: "narrow",
      value: row.key ?? "",
      placeholder: "Authorization",
      autocomplete: "off",
      spellcheck: "false",
      "aria-label": `请求头名（服务端 ${index + 1}，第 ${ri + 1} 行）`,
      oninput: (e) => {
        row.key = e.target.value;
        syncHeaderRows(index, entries);
      },
    });
    const valInput = el("input", {
      type: "text",
      value: row.value ?? "",
      placeholder: "Bearer xxxxxxxx",
      autocomplete: "off",
      spellcheck: "false",
      "aria-label": `请求头值（服务端 ${index + 1}，第 ${ri + 1} 行）`,
      oninput: (e) => {
        row.value = e.target.value;
        syncHeaderRows(index, entries);
      },
    });
    const del = el("button", {
      type: "button",
      class: "btn btn-icon btn-danger",
      text: "删除",
      "aria-label": `删除请求头 ${row.key || ri + 1}`,
      onclick: () => {
        entries.splice(ri, 1);
        syncHeaderRows(index, entries);
        render();
      },
    });
    list.append(el("div", { class: "header-row" }, [keyInput, valInput, del]));
  });

  const addBtn = el("button", {
    type: "button",
    class: "btn btn-small",
    text: "＋ 添加请求头",
    onclick: () => {
      entries.push({ key: "", value: "" });
      syncHeaderRows(index, entries);
      render();
    },
  });

  wrapEl.append(list);
  wrapEl.append(el("div", { class: "row", style: "margin-top:6px" }, addBtn));
  return wrapEl;
}

/** 把编辑中的 header 行同步回 server：_headerRows 保留编辑态，
 *  headers 存成后端要的 {头名: 值} map（丢掉空头名的行）。 */
function syncHeaderRows(index, entries) {
  const server = currentServers()[index];
  if (!server) return;
  server._headerRows = entries;
  const map = {};
  for (const { key, value } of entries) {
    const k = (key || "").trim();
    if (k) map[k] = value ?? "";
  }
  server.headers = map;
  markDirty();
}

function serverField(index, key, label, note, extraClass) {
  const servers = currentServers();
  const value = servers[index] ? servers[index][key] ?? "" : "";
  const input = el("input", {
    type: "text",
    class: extraClass || null,
    value: value,
    placeholder:
      key === "identity_header"
        ? "X-MCP-Identity"
        : key === "address"
          ? "https://your-host:8081/mcp"
          : "",
    autocomplete: "off",
    spellcheck: "false",
    "aria-label": `${label}（服务端 ${index + 1}）`,
    oninput: (e) => {
      const s = currentServers()[index];
      if (!s) return;
      s[key] = e.target.value;
      markDirty();
    },
  });
  return wrap(input, label, note);
}

function addServer() {
  const servers = ensureServers();
  // url 交给后端存成占位符；这里只维护明文 address
  servers.push({
    name: "",
    url: "",
    address: "",
    identity_header: "X-MCP-Identity",
    allow_tools: [],
    headers: {},
  });
  markDirty();
  render();
}

function removeServer(index) {
  const servers = currentServers();
  // 允许删光所有服务端 —— 不接工具就是纯 LLM 对话 bot
  const removed = servers[index];
  if (!confirm(`删除服务端「${removed?.name || index + 1}」？它勾选的工具也会一起去掉。`)) {
    return;
  }
  servers.splice(index, 1);
  if (removed?.name) delete state.probed[removed.name];
  markDirty();
  render();
}

function renderToolPicker() {
  const box = $("#tool-picker");
  box.replaceChildren();

  const servers = currentServers();
  if (!servers.length) return;

  for (const [index, server] of servers.entries()) {
    const block = el("div", { class: "server-block" });
    block.append(
      el("div", { class: "server-block-head" }, [
        el("h4", { text: `服务端：${server.name || "(未命名)"}` }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: "只探测这个",
          disabled: !server.name,
          title: server.name ? "" : "先填标识名",
          onclick: () => probeMcp(server.name),
        }),
      ])
    );

    const allow = Array.isArray(server.allow_tools) ? server.allow_tools : [];
    const probed = state.probed[server.name];

    if (!probed) {
      block.append(
        el("p", { class: "hint", text: "还没探测。已配置的白名单：" })
      );
      block.append(
        allow.length
          ? el("p", { class: "mono", text: allow.join("、") })
          : el("p", { class: "empty-list", text: "（空 = 全部工具都给模型）" })
      );
      box.append(block);
      continue;
    }

    // 探测到的工具 + 配置里有但服务端没有的（拼错或改名了）
    const names = probed.map((t) => t.name);
    const missing = allow.filter((name) => !names.includes(name));

    const allChecked = probed.length > 0 && probed.every((t) => allow.includes(t.name));
    block.append(
      el("div", { class: "tool-toolbar" }, [
        el("span", { class: "hint", text: `服务端提供 ${probed.length} 个，已勾选 ${allow.length} 个` }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: "全选",
          disabled: allChecked,
          onclick: () => {
            setServerAllow(index, probed.map((t) => t.name));
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: "全不选",
          disabled: allow.length === 0,
          onclick: () => {
            setServerAllow(index, []);
          },
        }),
      ])
    );

    if (missing.length) {
      block.append(
        note(
          "warn",
          `配置里这些工具服务端上没有，可能是拼错或中台改名了：${missing.join("、")}`
        )
      );
    }

    const list = el("div", { class: "tool-list" });
    for (const tool of probed) {
      const checkbox = el("input", {
        type: "checkbox",
        id: `tool-${index}-${tool.name}`,
        checked: allow.includes(tool.name),
        onchange: (e) => {
          const next = new Set(
            (Array.isArray(currentServers()[index].allow_tools)
              ? currentServers()[index].allow_tools
              : []
            )
          );
          if (e.target.checked) next.add(tool.name);
          else next.delete(tool.name);
          setServerAllow(index, Array.from(next));
        },
      });
      const params = tool.params.length
        ? tool.params
            .map((p) => p.name + (p.required ? "*" : ""))
            .join(", ")
        : "无参数";
      list.append(
        el("div", { class: "tool-row" }, [
          checkbox,
          el("label", { for: checkbox.id }, [
            el("div", { class: "tool-name", text: tool.name }),
            el("div", { class: "tool-desc", text: tool.description || "（这个工具没写描述，模型会不太好选）" }),
            el("div", { class: "tool-params", text: "参数：" + params }),
          ]),
        ])
      );
    }
    block.append(list);
    box.append(block);
  }
}

function setServerAllow(index, names) {
  const servers = currentServers();
  if (!servers[index]) return;
  servers[index].allow_tools = names;
  markDirty();
  render();
}

/** 所有探测到的工具（跨服务端），供身份绑定用 */
function allProbedTools() {
  const out = [];
  for (const tools of Object.values(state.probed)) {
    for (const tool of tools) out.push(tool);
  }
  return out;
}

// ---------------------------------------------------------------- 身份

function renderIdentity() {
  const box = $("#identity-fields");
  const presets = state.options.identity_presets;
  const pattern = getPath(state.values, "identity.pattern", "") ?? "";
  const known = presets.some((p) => p.value === pattern);

  const presetSelect = el("select", {
    id: "f-identity-preset",
    onchange: (e) => {
      if (e.target.value === "__custom__") return;
      setPath(state.values, "identity.pattern", e.target.value || null);
      markDirty();
      render();
    },
  });
  for (const preset of presets) {
    presetSelect.append(
      el("option", {
        value: preset.value,
        selected: preset.value === pattern,
        text: preset.label,
      })
    );
  }
  presetSelect.append(
    el("option", {
      value: "__custom__",
      selected: !known,
      text: "自定义正则…",
    })
  );

  box.replaceChildren(
    selectField({
      path: "identity.source",
      label: "怎么确定「是谁在说话」",
      choices: state.options.identity_sources,
      fallback: "wecom_userid",
    }),
    wrap(
      presetSelect,
      "工号格式校验",
      "格式不符的人会被直接拒绝，不会进模型也不会碰工具。不确定就先选「不校验」，" +
        "用密钥页的「等我 @ 一句话」看清真实工号长什么样再回来配。"
    )
  );

  if (!known) {
    box.append(
      textField({
        path: "identity.pattern",
        label: "自定义正则",
        note: "Python 正则语法，例如 ^[LM]\\d{6}$",
      })
    );
  }

  renderInjectRows();
}

function currentInject() {
  const inject = getPath(state.values, "identity.inject", []);
  return Array.isArray(inject) ? inject : [];
}

function renderInjectRows() {
  const box = $("#inject-rows");
  box.replaceChildren();

  const rows = currentInject();
  const tools = allProbedTools();
  const hint = $("#inject-hint");
  hint.textContent = tools.length
    ? ""
    : "先去「工具」页点探测，这里就能直接从真实参数里选";

  if (!rows.length) {
    box.append(
      el("p", {
        class: "empty-list",
        text: "还没有绑定。如果工具里有「提交人」「申请人」这类参数，强烈建议加一条。",
      })
    );
  }

  for (const [index, row] of rows.entries()) {
    const toolNames = tools.length ? tools.map((t) => t.name) : [row.tool].filter(Boolean);
    const selected = tools.find((t) => t.name === row.tool);
    const argNames = selected
      ? selected.params.map((p) => p.name)
      : [row.arg].filter(Boolean);

    const toolSelect = el("select", {
      "aria-label": "工具",
      onchange: (e) => {
        row.tool = e.target.value;
        const tool = tools.find((t) => t.name === row.tool);
        if (tool && !tool.params.some((p) => p.name === row.arg)) {
          row.arg = guessIdentityArg(tool) || "";
        }
        markDirty();
        render();
      },
    });
    for (const name of toolNames) {
      toolSelect.append(el("option", { value: name, selected: name === row.tool, text: name }));
    }
    if (!row.tool) toolSelect.prepend(el("option", { value: "", selected: true, text: "选一个工具…" }));

    const argSelect = el("select", {
      "aria-label": "参数",
      onchange: (e) => {
        row.arg = e.target.value;
        markDirty();
      },
    });
    for (const name of argNames) {
      argSelect.append(el("option", { value: name, selected: name === row.arg, text: name }));
    }
    if (!row.arg) argSelect.prepend(el("option", { value: "", selected: true, text: "选一个参数…" }));

    box.append(
      el("div", { class: "inject-row" }, [
        toolSelect,
        el("span", { class: "arrow", text: "的参数" }),
        argSelect,
        el("span", { class: "arrow", text: "= 当前用户" }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          "aria-label": "删除这条绑定",
          text: "删除",
          onclick: () => {
            currentInject().splice(index, 1);
            markDirty();
            render();
          },
        }),
      ])
    );
  }
}

/** 猜哪个参数是「操作人」，帮用户少点一次 */
function guessIdentityArg(tool) {
  const likely = /(work_code|workcode|user_?id|employee|applicant|staff|操作人|申请人|提交人)/i;
  const hit = tool.params.find((p) => likely.test(p.name));
  return hit ? hit.name : null;
}

// ---------------------------------------------------------------- 提示词

function renderPrompt() {
  const area = $("#prompt-text");
  if (area.value !== state.prompt) area.value = state.prompt;
  updatePromptMeta();
  renderPromptPreview();
  schedulePromptCheck();
}

function updatePromptMeta() {
  $("#prompt-meta").textContent =
    `${state.prompt.length} 字 · 保存到 ${getPath(state.values, "prompt.file", "prompt.md")}`;
}

/** 极简 Markdown 渲染：够看提示词排版就行，不追求完整规范 */
function renderMarkdown(src) {
  const escape = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const lines = (src || "").split("\n");
  const html = [];
  let inList = false;
  let inCode = false;

  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  for (const raw of lines) {
    if (raw.trim().startsWith("```")) {
      if (inCode) {
        html.push("</pre>");
        inCode = false;
      } else {
        closeList();
        html.push("<pre>");
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      html.push(escape(raw));
      continue;
    }

    const heading = raw.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${inline(escape(heading[2]))}</h${level}>`);
      continue;
    }

    const item = raw.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (item) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inline(escape(item[1]))}</li>`);
      continue;
    }

    closeList();
    if (raw.trim() === "") html.push("");
    else html.push(`<p>${inline(escape(raw))}</p>`);
  }
  closeList();
  if (inCode) html.push("</pre>");
  return html.join("\n");

  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
}

function renderPromptPreview() {
  const box = $("#prompt-rendered");
  if (!box) return;
  const text = state.prompt.trim();
  box.innerHTML = text
    ? renderMarkdown(state.prompt)
    : '<p class="empty-list">还没写提示词。左边写点东西，这里会实时预览。</p>';
}

let _promptCheckTimer = null;
function schedulePromptCheck() {
  clearTimeout(_promptCheckTimer);
  _promptCheckTimer = setTimeout(runPromptCheck, 400);
}

async function runPromptCheck() {
  const box = $("#prompt-check");
  if (!box) return;
  try {
    const report = await api("POST", "/api/prompt/check", { prompt: state.prompt });
    renderPromptCheck(report);
  } catch {
    // 检查失败不影响写提示词，静默即可
  }
}

function renderPromptCheck(report) {
  const box = $("#prompt-check");
  box.replaceChildren();

  box.append(
    el("p", {
      class: "check-score",
      text: `结构完整度 ${report.score}/${report.total} · 共 ${report.char_count} 字`,
    })
  );

  const list = el("ul", { class: "check-list" });
  for (const item of report.items) {
    list.append(
      el("li", { class: item.ok ? "ok" : "todo" }, [
        el("span", { class: "check-mark", text: item.ok ? "✓" : "○" }),
        el("span", {}, [
          el("strong", { text: item.label }),
          item.ok ? null : el("span", { class: "check-hint", text: " —— " + item.hint }),
        ]),
      ])
    );
  }
  box.append(list);

  if (report.suggestions.length) {
    box.append(el("p", { class: "check-tip-title", text: "可以再补充：" }));
    const tips = el("ul", { class: "check-tips" });
    for (const s of report.suggestions) tips.append(el("li", { text: s }));
    box.append(tips);
  } else {
    box.append(note("ok", "结构挺完整了。真正好不好用，去「预览测试」里聊两句看看。"));
  }
}

// ---------------------------------------------------------------- 会话

function renderSession() {
  $("#session-fields").replaceChildren(
    selectField({
      path: "session.scope",
      label: "谁跟谁共享对话上下文",
      choices: state.options.session_scopes,
      fallback: "group_user",
    }),
    textField({
      path: "session.max_turns",
      label: "记住最近几轮",
      type: "number",
      narrow: true,
      note: "1 轮 = 一问一答。调大会让每次请求携带的历史变长，token 成本上升",
    }),
    textField({
      path: "session.ttl_seconds",
      label: "多久没说话就忘掉（秒）",
      type: "number",
      narrow: true,
      note: "1800 = 半小时",
    })
  );
}

// ---------------------------------------------------------------- 文案

function renderMessages() {
  const box = $("#message-fields");
  box.replaceChildren();
  for (const field of state.options.message_fields) {
    box.append(
      textareaField({
        path: "messages." + field.key,
        label: field.label,
        note: field.note,
        rows: 2,
      })
    );
  }
  renderKeywords();
  renderProgress();
}

function renderKeywords() {
  const box = $("#keyword-editor");
  box.replaceChildren();

  let words = getPath(state.values, "messages.reset_keywords", null);
  if (!Array.isArray(words)) {
    words = [];
    setPath(state.values, "messages.reset_keywords", words);
  }

  for (const [index, word] of words.entries()) {
    box.append(
      el("div", { class: "list-row" }, [
        el("input", {
          type: "text",
          value: word,
          "aria-label": `重置关键词 ${index + 1}`,
          oninput: (e) => {
            words[index] = e.target.value;
            markDirty();
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          text: "删除",
          "aria-label": `删除关键词 ${word}`,
          onclick: () => {
            words.splice(index, 1);
            markDirty();
            render();
          },
        }),
      ])
    );
  }

  box.append(
    el("div", { class: "row" }, [
      el("button", {
        type: "button",
        class: "btn btn-small",
        text: "添加关键词",
        onclick: () => {
          words.push("");
          markDirty();
          render();
        },
      }),
    ])
  );
}

function renderProgress() {
  const box = $("#progress-editor");
  box.replaceChildren();

  let progress = getPath(state.values, "messages.tool_progress", null);
  if (typeof progress !== "object" || progress === null) {
    progress = { default: "正在调用工具..." };
    setPath(state.values, "messages.tool_progress", progress);
  }

  const toolNames = allProbedTools().map((t) => t.name);
  const entries = Object.entries(progress).filter(([k]) => k !== "default");

  for (const [tool, text] of entries) {
    const options = toolNames.length ? toolNames : [tool];
    const toolSelect = el("select", {
      "aria-label": "工具名",
      onchange: (e) => {
        const value = progress[tool];
        delete progress[tool];
        progress[e.target.value] = value;
        markDirty();
        render();
      },
    });
    for (const name of options) {
      toolSelect.append(el("option", { value: name, selected: name === tool, text: name }));
    }

    box.append(
      el("div", { class: "list-row" }, [
        toolSelect,
        el("input", {
          type: "text",
          value: text,
          "aria-label": `${tool} 的进度文案`,
          oninput: (e) => {
            progress[tool] = e.target.value;
            markDirty();
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          text: "删除",
          "aria-label": `删除 ${tool} 的进度文案`,
          onclick: () => {
            delete progress[tool];
            markDirty();
            render();
          },
        }),
      ])
    );
  }

  box.append(
    el("div", { class: "list-row" }, [
      el("input", {
        type: "text",
        value: progress.default ?? "",
        "aria-label": "其他工具的默认进度文案",
        oninput: (e) => {
          progress.default = e.target.value;
          markDirty();
        },
      }),
      el("span", { class: "hint", text: "← 其他工具都用这句" }),
    ])
  );

  const unset = toolNames.filter((name) => !(name in progress));
  if (unset.length) {
    box.append(
      el("div", { class: "row" }, [
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: "为某个工具单独写一句",
          onclick: () => {
            progress[unset[0]] = "";
            markDirty();
            render();
          },
        }),
      ])
    );
  }
}

// ---------------------------------------------------------------- 前置拦截

function currentIntercepts() {
  let rules = getPath(state.values, "intercepts", null);
  if (!Array.isArray(rules)) {
    rules = [];
    setPath(state.values, "intercepts", rules);
  }
  return rules;
}

function renderIntercepts() {
  const box = $("#intercepts-editor");
  if (!box) return;
  box.replaceChildren();

  const rules = currentIntercepts();

  if (!rules.length) {
    box.append(
      el("p", { class: "empty-list", text: "还没有拦截规则。点下面「添加规则」加一条。" })
    );
  }

  for (const [index, rule] of rules.entries()) {
    const card = el("div", { class: "server-card" });

    card.append(
      el("div", { class: "server-card-head" }, [
        el("strong", { text: `规则 ${index + 1}` }),
        el("button", {
          type: "button",
          class: "btn btn-icon btn-danger",
          text: "删除",
          "aria-label": `删除规则 ${index + 1}`,
          onclick: () => {
            rules.splice(index, 1);
            markDirty();
            render();
          },
        }),
      ])
    );

    // 关键词：逗号分隔的精确匹配词
    const matchInput = el("input", {
      type: "text",
      value: (Array.isArray(rule.match) ? rule.match : []).join(", "),
      placeholder: "帮助, help, ?",
      oninput: (e) => {
        rule.match = e.target.value
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean);
        markDirty();
      },
    });
    card.append(wrap(matchInput, "关键词（精确匹配，逗号分隔）", "整条消息完全等于其中一个词才命中"));

    // 正则
    const regexInput = el("input", {
      type: "text",
      value: rule.match_regex ?? "",
      placeholder: "^在吗[?？]?$",
      oninput: (e) => {
        const v = e.target.value.trim();
        if (v) rule.match_regex = v;
        else delete rule.match_regex;
        markDirty();
      },
    });
    card.append(wrap(regexInput, "正则匹配（可选）", "命中子串即算。和关键词至少填一个"));

    // 回复
    const replyArea = el("textarea", {
      rows: 3,
      placeholder: "命中时 Agent 直接回这句话",
      oninput: (e) => {
        rule.reply = e.target.value;
        markDirty();
      },
    });
    // textarea 的内容要用 .value 属性设置，不能走 el() 的 setAttribute("value")
    // —— <textarea> 会忽略 value 特性，只认 .value / 子文本节点，
    //    否则页面加载时命中回复显示为空，保存又把空值写回去（等于没生效）。
    replyArea.value = rule.reply ?? "";
    card.append(wrap(replyArea, "命中后的回复", "支持多行"));

    box.append(card);
  }

  box.append(
    el("div", { class: "row", style: "margin-top:12px" }, [
      el("button", {
        type: "button",
        class: "btn btn-small",
        text: "添加规则",
        onclick: () => {
          currentIntercepts().push({ match: [], reply: "" });
          markDirty();
          render();
        },
      }),
    ])
  );
}

// ---------------------------------------------------------------- 其他

function renderAdvanced() {
  $("#advanced-fields").replaceChildren(
    selectField({
      path: "logging.level",
      label: "日志详细程度",
      choices: state.options.log_levels.map((level) => ({
        value: level,
        label: level + (level === "INFO" ? "（默认）" : level === "DEBUG" ? "（排查问题时用）" : ""),
      })),
      fallback: "INFO",
    })
  );

  const box = $("#hooks-info");
  box.replaceChildren();
  const hooks = state.hooks;
  if (!hooks || !hooks.exists) {
    box.append(note("", "没有 hooks.py。绝大多数 Agent 不需要写代码。"));
  } else if (hooks.error) {
    box.append(note("bad", "hooks.py 加载失败：\n" + hooks.error));
  } else if (!hooks.implemented.length) {
    box.append(
      note("", `${hooks.file} 里所有扩展点都是注释状态，当前不生效。这是正常的。`)
    );
  } else {
    box.append(
      note("ok", `${hooks.file} 已启用的扩展点：${hooks.implemented.join("、")}`)
    );
  }
}

// ---------------------------------------------------------------- 总渲染

function render() {
  if (!state.current || !state.values) {
    $("#content").hidden = true;
    $("#empty-state").hidden = false;
    renderSidebar();
    return;
  }

  $("#content").hidden = false;
  $("#empty-state").hidden = true;
  $("#bot-title").textContent = state.current;
  $("#btn-delete").hidden = false;
  $("#run-hint").textContent = `python -m botkit run bots/${state.current}`;
  $("#btn-toggle-secrets").textContent = state.showSecrets ? "隐藏明文" : "显示明文";

  renderSidebar();
  renderTabs();
  renderSecrets();
  updateWecomTestButtons();
  renderLlm();
  renderTools();
  renderIdentity();
  renderPrompt();
  renderSkills();
  renderSession();
  renderMessages();
  renderIntercepts();
  renderApi();
  renderPreview();
  renderAdvanced();
}

// ---------------------------------------------------------------- 对外接口

function renderApi() {
  const box = $("#api-fields");
  if (!box) return;
  box.replaceChildren();

  const enabled = !!getPath(state.values, "api.enabled", false);

  const toggle = el("input", {
    type: "checkbox",
    id: "api-enabled",
    checked: enabled,
    onchange: (e) => {
      setPath(state.values, "api.enabled", e.target.checked);
      markDirty();
      render();
    },
  });
  box.append(
    el("div", { class: "row" }, [
      toggle,
      el("label", { for: "api-enabled", style: "font-weight:600;margin:0" }, [
        el("span", { text: "开启对外 chat 接口" }),
      ]),
    ])
  );
  box.append(
    el("p", {
      class: "desc",
      text:
        "开启后，运行 Agent 时会同时起一个 HTTP 服务，暴露 POST /chat 供 Dify " +
        "或自研门户调用。也可以只启动这个接口、不连企业微信（见下方启动说明）。",
    })
  );

  box.append(
    textField({
      path: "api.port",
      label: "监听端口",
      type: "number",
      narrow: true,
      note: "chat 接口监听的端口，默认 9000。多个 bot 同机部署时各用不同端口",
    })
  );
  box.append(
    textField({
      path: "api.token",
      label: "鉴权 token（API Key）",
      type: state.showSecrets ? "text" : "password",
      note: "调用方需在请求头带 Authorization: Bearer <token>。留空 = 不鉴权（仅限可信内网）。存进本地 .env、不进 git",
    })
  );
  box.append(
    el("div", { class: "row" }, [
      el("button", {
        type: "button",
        class: "btn btn-ghost btn-small",
        text: state.showSecrets ? "隐藏 token 明文" : "显示 token 明文",
        onclick: () => {
          state.showSecrets = !state.showSecrets;
          render();
        },
      }),
    ])
  );

  // 调用示例 + 启动说明
  const port = getPath(state.values, "api.port", 9000);
  box.append(el("h3", { text: "怎么调用" }));
  const example = el("pre", { class: "summary" });
  example.textContent =
    `# 启动（连企微 + 接口一起）:  python -m botkit run bots/${state.current}\n` +
    `# 只启动接口（不连企微）:     python -m botkit serve-api bots/${state.current}\n\n` +
    `POST http://<服务器IP>:${port}/chat\n` +
    `Authorization: Bearer <你填的token>\n` +
    `Content-Type: application/json\n\n` +
    `{"message": "帮我查下报销进度", "user": "L220104", "session_id": "conv-1"}\n\n` +
    `# 响应: {"reply": "...", "kind": "llm", "session_id": "conv-1"}\n` +
    `# 探活: GET http://<服务器IP>:${port}/health`;
  box.append(example);
}

// ---------------------------------------------------------------- 技能

/** 从共享目录拉 skill 清单，缓存到 state 后重渲染 */
async function loadSkillsCatalog() {
  try {
    const data = await api("GET", "/api/skills");
    state.skillsCatalog = Array.isArray(data.skills) ? data.skills : [];
    state.skillsLoaded = true;
  } catch (e) {
    state.skillsCatalog = [];
    state.skillsLoaded = false;
  }
  renderSkills();
}

function currentSkillFiles() {
  const files = getPath(state.values, "skills.files", []);
  return Array.isArray(files) ? files : [];
}

function renderSkills() {
  const box = $("#skills-panel");
  if (!box) return;
  box.replaceChildren();

  const enabled = !!getPath(state.values, "skills.enabled", false);
  const files = currentSkillFiles();

  // 总开关
  const toggle = el("input", {
    type: "checkbox",
    id: "skills-enabled",
    checked: enabled,
    onchange: (e) => {
      setPath(state.values, "skills.enabled", e.target.checked);
      markDirty();
      render();
    },
  });
  box.append(
    el("div", { class: "row" }, [
      toggle,
      el("label", { for: "skills-enabled", style: "font-weight:600;margin:0" }, [
        el("span", { text: "启用技能" }),
      ]),
    ])
  );
  box.append(
    el("p", {
      class: "desc",
      text:
        "启用后，勾选的技能文档会拼进系统提示词，帮 Agent 理解业务说明、术语。" +
        "文件放在共享目录 bots/_skills/，多个 Agent 可引用同一份。",
    })
  );

  // 共享 skill 列表（复选引用 + 删除）
  const catalog = state.skillsCatalog || [];
  if (!catalog.length) {
    box.append(
      el("p", { class: "empty-list", text: "共享目录还没有技能文件。用下面的按钮上传一个 .md。" })
    );
  } else {
    const list = el("div", { class: "tool-list" });
    for (const skill of catalog) {
      const checkbox = el("input", {
        type: "checkbox",
        id: `skill-${skill.name}`,
        checked: files.includes(skill.name),
        disabled: !enabled,
        onchange: (e) => {
          const next = new Set(currentSkillFiles());
          if (e.target.checked) next.add(skill.name);
          else next.delete(skill.name);
          setPath(state.values, "skills.files", Array.from(next));
          markDirty();
          render();
        },
      });
      const del = el("button", {
        type: "button",
        class: "btn btn-icon btn-danger",
        text: "删除",
        title: "从共享目录删除这个技能文件",
        onclick: () => deleteSkill(skill.name),
      });
      list.append(
        el("div", { class: "tool-row" }, [
          checkbox,
          el("label", { for: checkbox.id }, [
            el("div", { class: "tool-name", text: skill.name }),
            el("div", { class: "tool-desc", text: skill.summary || "（空文件）" }),
            el("div", {
              class: "tool-params",
              text:
                `${skill.chars} 字` +
                (skill.used_by && skill.used_by.length ? ` · 被 ${skill.used_by.join("、")} 引用` : ""),
            }),
          ]),
          del,
        ])
      );
    }
    box.append(list);
  }

  // 失效引用：勾选过、但共享目录里已经没有这个文件了（被删或改名）。
  // 不列出来的话页面上找不到地方取消勾选，这个 Agent 会一直校验失败。
  const known = new Set(catalog.map((s) => s.name));
  const stale = state.skillsLoaded ? files.filter((f) => !known.has(f)) : [];
  if (stale.length) {
    box.append(
      note("warn", "这些技能文件已经不在共享目录里了，但当前 Agent 还引用着，会导致校验失败。移除引用后记得保存。")
    );
    const staleList = el("div", { class: "tool-list" });
    for (const fname of stale) {
      staleList.append(
        el("div", { class: "tool-row" }, [
          el("div", { style: "flex:1;min-width:0" }, [
            el("div", { class: "tool-name", text: fname }),
            el("div", { class: "tool-desc", text: "文件不存在" }),
          ]),
          el("button", {
            type: "button",
            class: "btn btn-small",
            text: "移除引用",
            onclick: () => removeSkillRef(fname),
          }),
        ])
      );
    }
    box.append(staleList);
  }

  // 上传
  const fileInput = el("input", {
    type: "file",
    accept: ".md,text/markdown,text/plain",
    style: "display:none",
    id: "skill-upload-input",
    onchange: (e) => uploadSkillFile(e.target),
  });
  const uploadBtn = el("button", {
    type: "button",
    class: "btn btn-small",
    text: "＋ 上传技能 .md",
    onclick: () => fileInput.click(),
  });
  box.append(el("div", { class: "row", style: "margin-top:12px" }, [uploadBtn, fileInput]));
}

function uploadSkillFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const content = String(reader.result || "");
    let name = file.name;
    if (!/\.md$/i.test(name)) name = name.replace(/\.[^.]*$/, "") + ".md";
    try {
      const data = await api("POST", "/api/skills", { name, content });
      state.skillsCatalog = data.skills || [];
      setStatus(`已上传技能 ${data.name}`, "ok");
      renderSkills();
    } catch (e) {
      setStatus(e.message, "bad");
    }
  };
  reader.onerror = () => setStatus("读文件失败", "bad");
  reader.readAsText(file);
  input.value = "";
}

/** 从当前 Agent 的配置里去掉一条技能引用（需要保存才落盘） */
function removeSkillRef(name) {
  setPath(state.values, "skills.files", currentSkillFiles().filter((f) => f !== name));
  markDirty();
  render();
}

async function deleteSkill(name) {
  const skill = (state.skillsCatalog || []).find((s) => s.name === name);
  const users = (skill && skill.used_by) || [];
  const question = users.length
    ? `从共享目录删除技能「${name}」？\n\n这些 Agent 引用了它，会同时移除它们的引用：${users.join("、")}`
    : `从共享目录删除技能「${name}」？\n\n目前没有 Agent 引用它。`;
  if (!confirm(question)) {
    return;
  }
  try {
    const data = await api("DELETE", `/api/skills/${encodeURIComponent(name)}`);
    state.skillsCatalog = data.skills || [];
    const cleaned = data.cleaned || [];
    const skipped = data.skipped || [];

    // 当前页面上的配置同步去掉这条引用，否则一保存又会写回去
    const files = currentSkillFiles();
    if (state.current && files.includes(name)) {
      setPath(state.values, "skills.files", files.filter((f) => f !== name));
      // 后端没能替当前 Agent 改写文件时，要用户点保存落盘
      if (!cleaned.includes(state.current)) markDirty();
    }

    await loadBots();
    render();
    // 当前 Agent 的文件已被后端改写，刷新校验结果（清掉「技能文件不存在」的报错）
    if (state.current && cleaned.includes(state.current) && !state.dirty) {
      await revalidate();
    }

    let message = `已删除技能 ${name}`;
    if (cleaned.length) message += `，已从 ${cleaned.join("、")} 移除引用`;
    if (skipped.length) {
      message += `；${skipped.join("、")} 没能自动改写，请到对应 Agent 的「技能」页移除引用后保存`;
    }
    setStatus(message, skipped.length ? "bad" : "ok");
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

// ---------------------------------------------------------------- 预览测试

function renderPreview() {
  const box = $("#preview-chat");
  if (!box) return;
  box.replaceChildren();

  if (!state.preview.turns.length && !state.preview.busy) {
    box.append(
      el("p", { class: "empty-list", text: "还没开始。下面输入一句话发送试试。" })
    );
    return;
  }

  for (const turn of state.preview.turns) {
    if (turn.role === "user") {
      box.append(el("div", { class: "chat-bubble user", text: turn.text }));
      continue;
    }
    if (turn.role === "error") {
      box.append(note("bad", turn.text));
      continue;
    }
    // assistant turn: 工具轨迹 + 最终回复
    if (turn.tools && turn.tools.length) {
      const trace = el("div", { class: "chat-trace" });
      for (const call of turn.tools) {
        trace.append(
          el("details", { class: "tool-call" }, [
            el("summary", {}, [
              el("span", { class: "tool-call-name", text: "🔧 " + call.tool }),
            ]),
            el("div", { class: "tool-call-body" }, [
              el("div", { class: "tool-call-label", text: "参数" }),
              el("pre", { text: JSON.stringify(call.args, null, 2) }),
              el("div", { class: "tool-call-label", text: "返回" }),
              el("pre", {
                text: call.result + (call.truncated ? "\n…（已截断）" : ""),
              }),
            ]),
          ])
        );
      }
      box.append(trace);
    }
    box.append(el("div", { class: "chat-bubble bot", text: turn.text }));
  }

  if (state.preview.busy) {
    box.append(el("div", { class: "chat-bubble bot busy", text: "正在思考…（真调 LLM 和工具，可能要几秒）" }));
  }

  box.scrollTop = box.scrollHeight;
}

async function sendPreview() {
  if (state.preview.busy || !state.current) return;
  const input = $("#preview-message");
  const message = input.value.trim();
  if (!message) return;

  if (state.dirty) {
    if (!confirm("有配置改动还没保存。预览用的是已保存的配置，未保存的改动不会生效。\n\n继续预览？")) {
      return;
    }
  }

  state.preview.turns.push({ role: "user", text: message });
  state.preview.busy = true;
  input.value = "";
  renderPreview();

  const identity = $("#preview-identity").value.trim();

  try {
    const result = await api("POST", `/api/bots/${encodeURIComponent(state.current)}/preview`, {
      message,
      history: state.preview.history,
      identity: identity || undefined,
    });
    state.preview.history = result.history;
    state.preview.turns.push({
      role: "assistant",
      text: result.reply,
      tools: result.tool_calls,
    });
  } catch (e) {
    state.preview.turns.push({ role: "error", text: e.message });
  } finally {
    state.preview.busy = false;
    renderPreview();
  }
}

function clearPreview() {
  if (state.current) delete state.previews[state.current];
  state.preview = { history: [], turns: [], busy: false };
  renderPreview();
}

// ---------------------------------------------------------------- 数据加载

async function loadBots() {
  const data = await api("GET", "/api/bots");
  state.bots = data.bots;
  renderSidebar();
}

async function selectBot(name) {
  if (state.dirty && !confirm("有改动还没保存，切换会丢掉。确定切换吗？")) return;

  setStatus("正在读取…", "busy");
  try {
    const data = await api("GET", `/api/bots/${encodeURIComponent(name)}`);
    state.current = data.name;
    state.values = data.values || {};
    state.prompt = data.prompt || "";
    state.env = data.env || [];
    state.hooks = data.hooks;
    state.probed = {};
    // 恢复该 Agent 上次的预览对话（切走再切回不丢）；没有就开一段新的
    state.preview = state.previews[data.name] || { history: [], turns: [], busy: false };
    state.previews[data.name] = state.preview;
    state.dirty = false;
    // 保留用户当前所在的 tab，不强制回「密钥」页
    clearProbeResults();
    render();
    applyValidation(data.validation);
    updateWecomTestButtons();
    // 技能清单是共享的，选中 bot 后拉一次（不阻塞主渲染）
    loadSkillsCatalog();
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

/** 清掉上一个 Agent 残留的探测/测试结果框（企微、MCP），避免串台 */
function clearProbeResults() {
  for (const id of ["#wecom-result", "#mcp-result"]) {
    const box = $(id);
    if (box) box.replaceChildren();
  }
}

function applyValidation(validation) {
  if (!validation) return;
  $("#summary").textContent = validation.summary || "（校验未通过，没有摘要）";
  if (validation.ok) {
    setStatus("配置校验通过", "ok");
    clearBanner();
  } else {
    setStatus("配置有问题，看上方提示", "bad");
    showBanner(validation.error);
  }
}

function showBanner(text) {
  clearBanner();
  const banner = note("bad", "配置校验没通过：\n\n" + text);
  banner.id = "validation-banner";
  $(".panels").prepend(banner);
}

function clearBanner() {
  document.getElementById("validation-banner")?.remove();
}

// ---------------------------------------------------------------- 动作

/** 提交给后端的 values 副本：剥掉纯前端的编辑态字段（如 _headerRows）。 */
function valuesForSave() {
  const copy = JSON.parse(JSON.stringify(state.values || {}));
  const servers = copy?.mcp?.servers;
  if (Array.isArray(servers)) {
    for (const s of servers) {
      if (s && typeof s === "object") delete s._headerRows;
    }
  }
  return copy;
}

async function save() {
  if (!state.current) return;
  const button = $("#btn-save");
  button.disabled = true;
  setStatus("正在保存…", "busy");

  const env = {};
  for (const item of state.env) env[item.key] = item.value;

  try {
    const result = await api("PUT", `/api/bots/${encodeURIComponent(state.current)}`, {
      values: valuesForSave(),
      prompt: state.prompt,
      env,
    });
    state.dirty = false;
    applyValidation(result.validation);
    await loadBots();
    return true;
  } catch (e) {
    setStatus(e.message, "bad");
    showBanner(e.message);
    return false;
  } finally {
    button.disabled = false;
  }
}

async function revalidate() {
  if (!state.current) return;
  setStatus("正在校验…", "busy");
  try {
    applyValidation(
      await api("POST", `/api/bots/${encodeURIComponent(state.current)}/validate`)
    );
    await loadBots();
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

async function probeMcp(serverName) {
  const button = $("#btn-probe-mcp");
  const box = $("#mcp-result");
  const only = typeof serverName === "string" ? serverName : null;

  // 探测读的是磁盘上的 bot.yaml。页面上改了服务端名/地址但没保存的话，
  // 后端还按旧配置找，会报「配置里没有名叫 X 的 MCP 服务端」。
  // 所以有未保存改动时先落盘，保存失败（比如校验没过）就别往下探了。
  if (state.dirty) {
    box.replaceChildren(note("busy", "检测到未保存的改动，正在先保存…"));
    const ok = await save();
    if (!ok) {
      box.replaceChildren(note("bad", "保存没成功，先按上面的提示把配置改对再探测。"));
      return;
    }
  }

  button.disabled = true;
  box.replaceChildren(
    note("busy", only ? `正在连接服务端「${only}」…` : "正在连接全部 MCP 服务端…")
  );

  try {
    const query = only ? `?server=${encodeURIComponent(only)}` : "";
    const result = await api(
      "POST",
      `/api/bots/${encodeURIComponent(state.current)}/probe/mcp${query}`
    );
    // 只探单个时保留其他服务端已有的探测结果，别清掉
    if (!only) state.probed = {};

    box.replaceChildren();
    for (const server of result.servers) {
      if (server.ok) {
        state.probed[server.server] = server.tools;
        box.append(
          note("ok", `${server.server}：连通，拿到 ${server.tools.length} 个工具`)
        );
      } else {
        delete state.probed[server.server];
        box.append(note("bad", `${server.server} 连不上：\n${server.error}`));
      }
    }
    box.append(
      el("p", { class: "hint", text: `探测时使用的身份：${result.identity_used}` })
    );
    render();
  } catch (e) {
    box.replaceChildren(note("bad", e.message));
  } finally {
    button.disabled = false;
  }
}

async function probeWecom(wait) {
  const buttons = [$("#btn-test-wecom"), $("#btn-wait-wecom")];
  const box = $("#wecom-result");
  buttons.forEach((b) => (b.disabled = true));
  box.replaceChildren(
    note(
      "busy",
      wait
        ? "已连上，请现在切到企业微信，@ 这个 Agent 发一句话（最多等 90 秒）…"
        : "正在连接企业微信…"
    )
  );

  try {
    const result = await api(
      "POST",
      `/api/bots/${encodeURIComponent(state.current)}/probe/wecom?wait=${wait ? 1 : 0}`
    );
    box.replaceChildren(note("ok", "企微凭证正确，Agent 能连上。"));

    if (wait && result.timed_out) {
      box.append(
        note("warn", "等了 90 秒没收到消息。检查：Agent 在群里吗？群里发消息时 @ 它了吗？")
      );
    } else if (result.message) {
      box.append(renderWecomMessage(result.message));
    }
  } catch (e) {
    box.replaceChildren(note("bad", e.message));
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function renderWecomMessage(info) {
  const box = el("div");
  const list = el("dl", { class: "kv" });
  const add = (label, value) => {
    list.append(el("dt", { text: label }));
    list.append(el("dd", { text: value === "" ? "（空）" : String(value) }));
  };
  add("识别到的用户 ID", info.userid);
  add("看起来像加密串吗", info.looks_encrypted ? "像（需要映射）" : "不像，是明文");
  add("会话类型", info.chattype === "group" ? "群聊" : "单聊");
  add("你说的话", info.content);
  add("去掉 @ 之后", info.stripped);
  add("会话隔离 key", info.session_key);
  box.append(list);

  if (info.identity_ok) {
    box.append(
      note("ok", "身份校验通过。这个用户能正常使用 Agent。")
    );
  } else if (info.looks_encrypted) {
    box.append(
      note(
        "bad",
        "身份校验不通过，而且这个 ID 像加密串。\n\n" +
          "两条路：\n" +
          "1）（推荐）让超管重建企微机器人，一般就能拿到明文工号；\n" +
          "2）在 hooks.py 里实现 resolve_identity 做「加密 ID → 工号」的映射，" +
          "并把身份来源改成「自定义」。"
      )
    );
  } else {
    box.append(
      note(
        "warn",
        `身份校验不通过：${info.userid} 匹配不上 ${info.pattern}。\n` +
          "这个 ID 看起来是明文的，多半是格式校验写严了 —— 去「身份与安全」页改一下。"
      )
    );
  }
  return box;
}

async function createBot() {
  const dialog = $("#new-dialog");
  const input = $("#new-name");
  const error = $("#new-error");
  const name = input.value.trim();

  error.textContent = "";
  if (!name) {
    error.textContent = "请输入名字";
    return;
  }

  try {
    const result = await api("POST", "/api/bots", { name });
    dialog.close();
    input.value = "";
    await loadBots();
    state.dirty = false;
    await selectBot(result.name);
    setStatus("已创建，先在「密钥」页填企微凭证和 MCP 地址", "busy");
  } catch (e) {
    error.textContent = e.message;
  }
}

async function deleteBot() {
  if (!state.current) return;
  const name = state.current;
  if (
    !confirm(
      `确定删除 Agent「${name}」吗？\n\n` +
        "它会被移到 bots/.trash/ 回收站，不是彻底删除，误删了可以从那里找回。"
    )
  ) {
    return;
  }

  setStatus("正在删除…", "busy");
  try {
    await api("DELETE", `/api/bots/${encodeURIComponent(name)}`);
    state.current = null;
    state.values = null;
    state.dirty = false;
    await loadBots();
    // 删完自动切到还剩的第一个，没有就回到空状态
    if (state.bots.length) {
      await selectBot(state.bots[0].name);
    } else {
      render();
    }
    setStatus(`已删除「${name}」（在回收站里可找回）`, "ok");
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

async function exportBot() {
  if (!state.current) return;
  setStatus("正在导出…", "busy");
  try {
    const data = await api("GET", `/api/bots/${encodeURIComponent(state.current)}/export`);
    const text = JSON.stringify(data, null, 2);
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `${state.current}.bot.json` });
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus("已导出 JSON（不含密钥值，导入后需在密钥页填）", "ok");
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

async function submitImport() {
  const dialog = $("#import-dialog");
  const error = $("#import-error");
  const text = $("#import-text").value.trim();
  error.textContent = "";
  if (!text) {
    error.textContent = "先粘贴 JSON 或选一个文件";
    return;
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (e) {
    error.textContent = "这不是合法的 JSON：" + e.message;
    return;
  }
  try {
    const result = await api("POST", "/api/bots/import", payload);
    dialog.close();
    $("#import-text").value = "";
    await loadBots();
    state.dirty = false;
    await selectBot(result.name);
    setStatus("已导入，去「密钥」页填企微凭证和 MCP 地址/token", "busy");
  } catch (e) {
    error.textContent = e.message;
  }
}

/** 选文件后把内容读进导入框 */
function loadImportFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#import-text").value = String(reader.result || "");
    $("#import-error").textContent = "";
  };
  reader.onerror = () => {
    $("#import-error").textContent = "读文件失败";
  };
  reader.readAsText(file);
}

// ---------------------------------------------------------------- 启动

async function init() {
  $("#btn-save").addEventListener("click", save);
  $("#btn-delete").addEventListener("click", deleteBot);
  $("#btn-validate").addEventListener("click", revalidate);
  $("#btn-probe-mcp").addEventListener("click", () => probeMcp());
  $("#btn-add-server").addEventListener("click", addServer);
  $("#btn-test-wecom").addEventListener("click", () => probeWecom(false));
  $("#btn-wait-wecom").addEventListener("click", () => probeWecom(true));
  $("#btn-add-inject").addEventListener("click", () => {
    currentInject().push({ tool: "", arg: "" });
    if (!getPath(state.values, "identity.inject")) {
      setPath(state.values, "identity.inject", currentInject());
    }
    markDirty();
    render();
  });
  $("#btn-toggle-secrets").addEventListener("click", () => {
    state.showSecrets = !state.showSecrets;
    render();
  });

  $("#prompt-text").addEventListener("input", (e) => {
    state.prompt = e.target.value;
    markDirty();
    updatePromptMeta();
    renderPromptPreview();
    schedulePromptCheck();
  });

  // 预览测试
  $("#btn-preview-send").addEventListener("click", sendPreview);
  $("#btn-preview-clear").addEventListener("click", clearPreview);
  $("#preview-message").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendPreview();
    }
  });

  // 预览 / 结构检查切换
  for (const tab of $$(".preview-tab")) {
    tab.addEventListener("click", () => {
      for (const t of $$(".preview-tab")) t.classList.toggle("active", t === tab);
      const showCheck = tab.dataset.view === "check";
      $("#prompt-rendered").hidden = showCheck;
      $("#prompt-check").hidden = !showCheck;
      if (showCheck) runPromptCheck();
    });
  }

  $("#btn-export").addEventListener("click", exportBot);
  $("#btn-import").addEventListener("click", () => {
    $("#import-error").textContent = "";
    $("#import-text").value = "";
    $("#import-dialog").showModal();
  });
  $("#import-cancel").addEventListener("click", () => $("#import-dialog").close());
  $("#import-file").addEventListener("change", (e) => loadImportFile(e.target));
  $("#import-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitImport();
  });

  $("#btn-new").addEventListener("click", () => {
    $("#new-error").textContent = "";
    $("#new-dialog").showModal();
    $("#new-name").focus();
  });
  $("#new-cancel").addEventListener("click", () => $("#new-dialog").close());
  $("#new-form").addEventListener("submit", (e) => {
    e.preventDefault();
    createBot();
  });

  // Ctrl+S 保存
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  try {
    state.options = await api("GET", "/api/options");
    await loadBots();
    if (state.bots.length) await selectBot(state.bots[0].name);
    else render();
  } catch (e) {
    setStatus("初始化失败：" + e.message, "bad");
  }
}

init();

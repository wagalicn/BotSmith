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
  usage: null,       // 当前 Agent 当前月的用量 {name, months, current, summary, detail}
  usageLoading: false,
};

const TAB_IDS = [
  "secrets", "llm", "tools", "identity", "prompt", "skills",
  "session", "messages", "intercepts", "api", "preview", "usage", "advanced",
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
  // 带上当前界面语言，让后端（校验报错 / 配置摘要）按同一语言返回
  const options = { method, headers: { "Accept-Language": getLang() === "en" ? "en" : "zh" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new Error(t("api.non_json", { status: response.status }));
  }
  if (!response.ok) throw new Error(payload?.error || t("api.req_fail", { status: response.status }));
  return payload;
}

function setStatus(text, kind = "") {
  const node = $("#status");
  node.textContent = text;
  node.className = "status" + (kind ? " " + kind : "");
}

function markDirty() {
  state.dirty = true;
  setStatus(t("status.dirty"), "busy");
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

/**
 * 取后端 options 某组，经 i18n 翻译后给 selectField 用。
 * identity_presets 用 value 作键，其余用 value 作键（都是稳定标识）。
 */
function optionChoices(group) {
  const raw = state.options[group] || [];
  return raw.map((item) => {
    const id = item.value !== undefined ? item.value : item.key;
    const tr = translateOption(group, id, item);
    return { value: item.value, label: tr.label, note: tr.note };
  });
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
    list.append(el("li", { class: "empty-list", text: t("sidebar.empty") }));
    return;
  }

  for (const bot of state.bots) {
    const button = el("button", {
      type: "button",
      class: "bot-item" + (bot.name === state.current ? " active" : ""),
      onclick: () => selectBot(bot.name),
      title: bot.ok ? t("status.validate_ok") : bot.error,
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
  for (const id of TAB_IDS) {
    tabs.append(
      el("button", {
        type: "button",
        class: "tab" + (id === state.activeTab ? " active" : ""),
        onclick: () => {
          state.activeTab = id;
          render();
        },
        text: t("tab." + id),
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
    btn.title = ready ? "" : t("secrets.wecom_btn_hint");
  }
}

function renderSecrets() {
  const box = $("#env-fields");
  box.replaceChildren();

  if (!state.env.length) {
    box.append(el("p", { class: "empty-list", text: t("secrets.empty") }));
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
      field.append(el("p", { class: "field-error", text: t("secrets.not_filled") }));
    }
    box.append(field);
  }
}

// 环境变量的提示文案 —— 用 i18n key，zh/en 都在 i18n.js 的字典里
const ENV_HINT_KEYS = {
  WECHAT_BOT_ID: "env.WECHAT_BOT_ID",
  WECHAT_BOT_SECRET: "env.WECHAT_BOT_SECRET",
  LLM_API_URL: "env.LLM_API_URL",
  LLM_API_KEY: "env.LLM_API_KEY",
  LLM_MODEL: "env.LLM_MODEL",
  LLM_HTTP_PROXY: "env.LLM_HTTP_PROXY",
  // MCP 地址不在这里 —— 它在「工具」页每个服务端旁边填
};

function envHint(key) {
  const i18nKey = ENV_HINT_KEYS[key];
  return i18nKey ? t(i18nKey) : "";
}

// ---------------------------------------------------------------- 大模型

function renderLlm() {
  const box = $("#llm-fields");
  const client = getPath(state.values, "llm.client", "openai");
  box.replaceChildren(
    selectField({
      path: "llm.client",
      label: t("llm.client_label"),
      choices: optionChoices("llm_clients"),
      fallback: "openai",
      note: t("llm.client_note"),
    }),
    textField({
      path: "llm.api_url",
      label: t("llm.url_label"),
      placeholder: "https://xxx.com/compatible-mode/v1",
      note: t("llm.url_note"),
    }),
    textField({
      path: "llm.api_key",
      label: t("llm.key_label"),
      type: state.showSecrets ? "text" : "password",
      note: t("llm.key_note"),
    }),
    textField({
      path: "llm.model",
      label: t("llm.model_label"),
      placeholder: "qwen3.7-plus",
      note: t("llm.model_note"),
    }),
    textField({
      path: "llm.timeout_seconds",
      label: t("llm.timeout_label"),
      type: "number",
      narrow: true,
      note: t("llm.timeout_note"),
    }),
    textField({
      path: "llm.max_tool_rounds",
      label: t("llm.rounds_label"),
      type: "number",
      narrow: true,
      note: t("llm.rounds_note"),
    }),
    textField({
      path: "llm.max_tokens",
      label: t("llm.max_tokens_label"),
      type: "number",
      narrow: true,
      placeholder: "8192",
      note: t("llm.max_tokens_note"),
    })
  );

  // HTTP 代理（可选）：勾选才启用。不勾选时 llm 段里不保留 http_proxy 这个键，
  // 生成的 bot.yaml 里也就没有这行 —— 没用代理的配置保持干净。
  const llmSection = state.values.llm || {};
  const proxyOn = Object.prototype.hasOwnProperty.call(llmSection, "http_proxy");
  const proxyToggle = el("input", {
    type: "checkbox",
    id: "llm-proxy-enabled",
    checked: proxyOn,
    onchange: (e) => {
      const llm = state.values.llm || (state.values.llm = {});
      if (e.target.checked) {
        if (!llm.http_proxy) llm.http_proxy = "";
      } else {
        delete llm.http_proxy;
      }
      markDirty();
      render();
    },
  });
  box.append(
    el("div", { class: "row" }, [
      proxyToggle,
      el(
        "label",
        { for: "llm-proxy-enabled", style: "font-weight:600;margin:0" },
        [el("span", { text: t("llm.proxy_enable") })]
      ),
    ]),
    el("p", { class: "desc", text: t("llm.proxy_enable_desc") })
  );
  if (proxyOn) {
    box.append(
      textField({
        path: "llm.http_proxy",
        label: t("llm.proxy_label"),
        placeholder: "http://127.0.0.1:7890",
        note: t("llm.proxy_note"),
      })
    );
  }

  // 显示/隐藏 api_key 明文（复用全局 showSecrets）
  box.append(
    el("div", { class: "row" }, [
      el("button", {
        type: "button",
        class: "btn btn-ghost btn-small",
        text: state.showSecrets ? t("llm.hide_key") : t("llm.show_key"),
        onclick: () => {
          state.showSecrets = !state.showSecrets;
          render();
        },
      }),
    ])
  );

  if (client === "aiohttp") {
    box.prepend(note("warn", t("llm.aiohttp_warn")));
  } else if (client === "anthropic") {
    box.prepend(note("warn", t("llm.anthropic_warn")));
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
      el("p", { class: "empty-list", text: t("tools.no_servers") })
    );
    return;
  }

  servers.forEach((server, index) => {
    const card = el("div", { class: "server-card" });

    const head = el("div", { class: "server-card-head" }, [
      el("strong", { text: t("tools.server_n", { n: index + 1 }) }),
      el("button", {
        type: "button",
        class: "btn btn-icon btn-danger",
        text: t("tools.delete"),
        "aria-label": t("tools.delete") + " " + (server.name || index + 1),
        title: t("tools.delete_server_title"),
        onclick: () => removeServer(index),
      }),
    ]);
    card.append(head);

    card.append(
      serverField(index, "name", t("tools.field_name_label"), t("tools.field_name_note"), "narrow")
    );
    card.append(
      serverField(index, "address", t("tools.field_addr_label"), t("tools.field_addr_note"))
    );
    card.append(
      serverField(index, "identity_header", t("tools.field_header_label"), t("tools.field_header_note"))
    );
    card.append(renderServerHeaders(index));

    box.append(card);
  });
}

/** 额外固定请求头（如 Authorization: Bearer xxx）。探测和运行都会带上。
 *  值多是 token，保存时会落到本地 .env、不进 git、也不在密钥页显示。 */
function renderServerHeaders(index) {
  const wrapEl = el("div", { class: "field server-headers" });
  wrapEl.append(el("label", { class: "field-label", text: t("tools.extra_headers_label") }));
  wrapEl.append(el("p", { class: "field-note", text: t("tools.extra_headers_note") }));

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
      "aria-label": t("tools.extra_headers_label") + " " + (index + 1) + "-" + (ri + 1),
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
      "aria-label": t("tools.extra_headers_label") + " value " + (index + 1) + "-" + (ri + 1),
      oninput: (e) => {
        row.value = e.target.value;
        syncHeaderRows(index, entries);
      },
    });
    const del = el("button", {
      type: "button",
      class: "btn btn-icon btn-danger",
      text: t("tools.delete"),
      "aria-label": t("tools.delete") + " " + (row.key || ri + 1),
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
    text: t("tools.add_header"),
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
    "aria-label": `${label} (${index + 1})`,
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
  if (!confirm(t("tools.confirm_delete_server", { name: removed?.name || index + 1 }))) {
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
        el("h4", { text: t("tools.server_colon", { name: server.name || t("tools.unnamed") }) }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: t("tools.probe_only_this"),
          disabled: !server.name,
          title: server.name ? "" : t("tools.probe_only_need_name"),
          onclick: () => probeMcp(server.name),
        }),
      ])
    );

    const allow = Array.isArray(server.allow_tools) ? server.allow_tools : [];
    const probed = state.probed[server.name];

    if (!probed) {
      block.append(
        el("p", { class: "hint", text: t("tools.not_probed") })
      );
      block.append(
        allow.length
          ? el("p", { class: "mono", text: allow.join("、") })
          : el("p", { class: "empty-list", text: t("tools.allow_empty") })
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
        el("span", { class: "hint", text: t("tools.provide_selected", { total: probed.length, count: allow.length }) }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: t("tools.select_all"),
          disabled: allChecked,
          onclick: () => {
            setServerAllow(index, probed.map((tool) => tool.name));
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: t("tools.select_none"),
          disabled: allow.length === 0,
          onclick: () => {
            setServerAllow(index, []);
          },
        }),
      ])
    );

    if (missing.length) {
      block.append(note("warn", t("tools.missing_tools", { names: missing.join("、") })));
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
        : t("tools.no_params");
      list.append(
        el("div", { class: "tool-row" }, [
          checkbox,
          el("label", { for: checkbox.id }, [
            el("div", { class: "tool-name", text: tool.name }),
            el("div", { class: "tool-desc", text: tool.description || t("tools.no_desc") }),
            el("div", { class: "tool-params", text: t("tools.params_prefix", { params }) }),
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
  const presets = optionChoices("identity_presets");
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
      text: t("identity.custom_regex_option"),
    })
  );

  box.replaceChildren(
    selectField({
      path: "identity.source",
      label: t("identity.source_label"),
      choices: optionChoices("identity_sources"),
      fallback: "wecom_userid",
    }),
    wrap(presetSelect, t("identity.pattern_label"), t("identity.pattern_note"))
  );

  if (!known) {
    box.append(
      textField({
        path: "identity.pattern",
        label: t("identity.custom_regex_label"),
        note: t("identity.custom_regex_note"),
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
  hint.textContent = tools.length ? "" : t("identity.inject_hint_probe");

  if (!rows.length) {
    box.append(
      el("p", { class: "empty-list", text: t("identity.inject_empty") })
    );
  }

  for (const [index, row] of rows.entries()) {
    const toolNames = tools.length ? tools.map((t) => t.name) : [row.tool].filter(Boolean);
    const selected = tools.find((t) => t.name === row.tool);
    const argNames = selected
      ? selected.params.map((p) => p.name)
      : [row.arg].filter(Boolean);

    const toolSelect = el("select", {
      "aria-label": t("identity.tool_aria"),
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
    if (!row.tool) toolSelect.prepend(el("option", { value: "", selected: true, text: t("identity.pick_tool") }));

    const argSelect = el("select", {
      "aria-label": t("identity.arg_aria"),
      onchange: (e) => {
        row.arg = e.target.value;
        markDirty();
      },
    });
    for (const name of argNames) {
      argSelect.append(el("option", { value: name, selected: name === row.arg, text: name }));
    }
    if (!row.arg) argSelect.prepend(el("option", { value: "", selected: true, text: t("identity.pick_arg") }));

    box.append(
      el("div", { class: "inject-row" }, [
        toolSelect,
        el("span", { class: "arrow", text: t("identity.inject_mid") }),
        argSelect,
        el("span", { class: "arrow", text: t("identity.inject_tail") }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          "aria-label": t("identity.inject_delete"),
          text: t("identity.inject_delete"),
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
  $("#prompt-meta").textContent = t("prompt.meta", {
    chars: state.prompt.length,
    file: getPath(state.values, "prompt.file", "prompt.md"),
  });
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
    : `<p class="empty-list">${t("prompt.empty_preview")}</p>`;
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
      text: t("prompt.check_score", { score: report.score, total: report.total, chars: report.char_count }),
    })
  );

  // 后端 item 有稳定 key（role/task/reply/flow/constraint）；用它查 i18n，
  // 没有对应翻译键时回退到后端返回的原始 label/hint。
  const itemLabel = (item) => {
    const k = "check." + item.key + ".label";
    const v = t(k);
    return v === k ? item.label : v;
  };
  const itemHint = (item) => {
    const k = "check." + item.key + ".hint";
    const v = t(k);
    return v === k ? item.hint : v;
  };

  const list = el("ul", { class: "check-list" });
  for (const item of report.items) {
    list.append(
      el("li", { class: item.ok ? "ok" : "todo" }, [
        el("span", { class: "check-mark", text: item.ok ? "✓" : "○" }),
        el("span", {}, [
          el("strong", { text: itemLabel(item) }),
          item.ok ? null : el("span", { class: "check-hint", text: " —— " + itemHint(item) }),
        ]),
      ])
    );
  }
  box.append(list);

  // suggestions 在前端按当前语言生成（复刻后端 prompt_check 的判断逻辑），
  // 这样英文界面下建议也是英文。后端返回的中文 suggestions 不再直接使用。
  const suggestions = buildPromptSuggestions(report, itemLabel, itemHint);
  if (suggestions.length) {
    box.append(el("p", { class: "check-tip-title", text: t("prompt.check_can_add") }));
    const tips = el("ul", { class: "check-tips" });
    for (const s of suggestions) tips.append(el("li", { text: s }));
    box.append(tips);
  } else {
    box.append(note("ok", t("prompt.check_ok")));
  }
}

/** 按当前语言生成提示词结构建议，逻辑对齐后端 prompt_check.analyze_prompt */
function buildPromptSuggestions(report, itemLabel, itemHint) {
  const out = [];
  if (report.char_count === 0) {
    out.push(t("check.sug.empty"));
  } else if (report.char_count < 60) {
    out.push(t("check.sug.short"));
  }
  if (report.char_count > 0 && !report.heading_count) {
    out.push(t("check.sug.no_heading"));
  }
  for (const item of report.items) {
    if (!item.ok) {
      out.push(t("check.sug.missing", { label: itemLabel(item), hint: itemHint(item) }));
    }
  }
  return out;
}

// ---------------------------------------------------------------- 会话

function renderSession() {
  $("#session-fields").replaceChildren(
    selectField({
      path: "session.scope",
      label: t("session.scope_label"),
      choices: optionChoices("session_scopes"),
      fallback: "group_user",
    }),
    textField({
      path: "session.max_turns",
      label: t("session.max_turns_label"),
      type: "number",
      narrow: true,
      note: t("session.max_turns_note"),
    }),
    textField({
      path: "session.ttl_seconds",
      label: t("session.ttl_label"),
      type: "number",
      narrow: true,
      note: t("session.ttl_note"),
    })
  );
}

// ---------------------------------------------------------------- 文案

function renderMessages() {
  const box = $("#message-fields");
  box.replaceChildren();
  for (const field of state.options.message_fields) {
    const tr = translateOption("message_fields", field.key, field);
    box.append(
      textareaField({
        path: "messages." + field.key,
        label: tr.label,
        note: tr.note,
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
          "aria-label": t("messages.keywords_h3") + " " + (index + 1),
          oninput: (e) => {
            words[index] = e.target.value;
            markDirty();
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          text: t("messages.delete"),
          "aria-label": t("messages.delete") + " " + word,
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
        text: t("messages.add_keyword"),
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
    progress = {};
    setPath(state.values, "messages.tool_progress", progress);
  }

  const toolNames = allProbedTools().map((tool) => tool.name);
  const entries = Object.entries(progress).filter(([k]) => k !== "default");

  for (const [tool, text] of entries) {
    const options = toolNames.length ? toolNames : [tool];
    const toolSelect = el("select", {
      "aria-label": t("messages.progress_h3"),
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
          "aria-label": tool,
          oninput: (e) => {
            progress[tool] = e.target.value;
            markDirty();
          },
        }),
        el("button", {
          type: "button",
          class: "btn btn-icon",
          text: t("messages.delete"),
          "aria-label": t("messages.delete") + " " + tool,
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
        "aria-label": t("messages.progress_default_hint"),
        oninput: (e) => {
          progress.default = e.target.value;
          markDirty();
        },
      }),
      el("span", { class: "hint", text: t("messages.progress_default_hint") }),
    ])
  );

  const unset = toolNames.filter((name) => !(name in progress));
  if (unset.length) {
    box.append(
      el("div", { class: "row" }, [
        el("button", {
          type: "button",
          class: "btn btn-small",
          text: t("messages.progress_add_one"),
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
      el("p", { class: "empty-list", text: t("intercepts.empty") })
    );
  }

  for (const [index, rule] of rules.entries()) {
    const card = el("div", { class: "server-card" });

    card.append(
      el("div", { class: "server-card-head" }, [
        el("strong", { text: t("intercepts.rule_n", { n: index + 1 }) }),
        el("button", {
          type: "button",
          class: "btn btn-icon btn-danger",
          text: t("intercepts.delete"),
          "aria-label": t("intercepts.delete") + " " + (index + 1),
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
      placeholder: t("intercepts.match_placeholder"),
      oninput: (e) => {
        rule.match = e.target.value
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean);
        markDirty();
      },
    });
    card.append(wrap(matchInput, t("intercepts.match_label"), t("intercepts.match_note")));

    // 正则
    const regexInput = el("input", {
      type: "text",
      value: rule.match_regex ?? "",
      placeholder: t("intercepts.regex_placeholder"),
      oninput: (e) => {
        const v = e.target.value.trim();
        if (v) rule.match_regex = v;
        else delete rule.match_regex;
        markDirty();
      },
    });
    card.append(wrap(regexInput, t("intercepts.regex_label"), t("intercepts.regex_note")));

    // 回复
    const replyArea = el("textarea", {
      rows: 3,
      placeholder: t("intercepts.reply_placeholder"),
      oninput: (e) => {
        rule.reply = e.target.value;
        markDirty();
      },
    });
    // textarea 的内容要用 .value 属性设置，不能走 el() 的 setAttribute("value")
    // —— <textarea> 会忽略 value 特性，只认 .value / 子文本节点，
    //    否则页面加载时命中回复显示为空，保存又把空值写回去（等于没生效）。
    replyArea.value = rule.reply ?? "";
    card.append(wrap(replyArea, t("intercepts.reply_label"), t("intercepts.reply_note")));

    box.append(card);
  }

  box.append(
    el("div", { class: "row", style: "margin-top:12px" }, [
      el("button", {
        type: "button",
        class: "btn btn-small",
        text: t("intercepts.add_rule"),
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
      label: t("advanced.log_label"),
      choices: state.options.log_levels.map((level) => ({
        value: level,
        label: level + (level === "INFO" ? t("advanced.log_default") : level === "DEBUG" ? t("advanced.log_debug") : ""),
      })),
      fallback: "INFO",
    })
  );

  const box = $("#hooks-info");
  box.replaceChildren();
  const hooks = state.hooks;
  if (!hooks || !hooks.exists) {
    box.append(note("", t("advanced.hooks_none")));
  } else if (hooks.error) {
    box.append(note("bad", t("advanced.hooks_fail", { error: hooks.error })));
  } else if (!hooks.implemented.length) {
    box.append(note("", t("advanced.hooks_commented", { file: hooks.file })));
  } else {
    box.append(note("ok", t("advanced.hooks_active", { file: hooks.file, list: hooks.implemented.join("、") })));
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
  $("#btn-toggle-secrets").textContent = state.showSecrets ? t("secrets.toggle_hide") : t("secrets.toggle_show");

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
  renderUsage();
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
        el("span", { text: t("api.enable") }),
      ]),
    ])
  );
  box.append(el("p", { class: "desc", text: t("api.enable_desc") }));

  box.append(
    textField({
      path: "api.port",
      label: t("api.port_label"),
      type: "number",
      narrow: true,
      note: t("api.port_note"),
    })
  );
  box.append(
    textField({
      path: "api.token",
      label: t("api.token_label"),
      type: state.showSecrets ? "text" : "password",
      note: t("api.token_note"),
    })
  );
  box.append(
    el("div", { class: "row" }, [
      el("button", {
        type: "button",
        class: "btn btn-ghost btn-small",
        text: state.showSecrets ? t("api.hide_token") : t("api.show_token"),
        onclick: () => {
          state.showSecrets = !state.showSecrets;
          render();
        },
      }),
    ])
  );

  // 调用示例 + 启动说明（注释用英文占位，示例内容是技术文本，保持原样）
  const port = getPath(state.values, "api.port", 9000);
  box.append(el("h3", { text: t("api.how_h3") }));
  const example = el("pre", { class: "summary" });
  example.textContent =
    `# run (WeCom + API together):  python -m botkit run bots/${state.current}\n` +
    `# API only (no WeCom):         python -m botkit serve-api bots/${state.current}\n\n` +
    `POST http://<server-ip>:${port}/chat\n` +
    `Authorization: Bearer <your-token>\n` +
    `Content-Type: application/json\n\n` +
    `{"message": "check my expense status", "user": "L220104", "session_id": "conv-1"}\n\n` +
    `# response: {"reply": "...", "kind": "llm", "session_id": "conv-1"}\n` +
    `# health:   GET http://<server-ip>:${port}/health`;
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
        el("span", { text: t("skills.enable") }),
      ]),
    ])
  );
  box.append(el("p", { class: "desc", text: t("skills.enable_desc") }));

  // 共享 skill 列表（复选引用 + 删除）
  const catalog = state.skillsCatalog || [];
  if (!catalog.length) {
    box.append(
      el("p", { class: "empty-list", text: t("skills.empty") })
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
        text: t("tools.delete"),
        title: t("skills.delete_title"),
        onclick: () => deleteSkill(skill.name),
      });
      const usage =
        skill.used_by && skill.used_by.length
          ? t("skills.chars_used", { chars: skill.chars, users: skill.used_by.join("、") })
          : t("skills.chars_only", { chars: skill.chars });
      list.append(
        el("div", { class: "tool-row" }, [
          checkbox,
          el("label", { for: checkbox.id }, [
            el("div", { class: "tool-name", text: skill.name }),
            el("div", { class: "tool-desc", text: skill.summary || t("skills.empty_file") }),
            el("div", { class: "tool-params", text: usage }),
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
    box.append(note("warn", t("skills.stale_warn")));
    const staleList = el("div", { class: "tool-list" });
    for (const fname of stale) {
      staleList.append(
        el("div", { class: "tool-row" }, [
          el("div", { style: "flex:1;min-width:0" }, [
            el("div", { class: "tool-name", text: fname }),
            el("div", { class: "tool-desc", text: t("skills.file_missing") }),
          ]),
          el("button", {
            type: "button",
            class: "btn btn-small",
            text: t("skills.remove_ref"),
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
    text: t("skills.upload"),
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
      setStatus(t("skills.uploaded", { name: data.name }), "ok");
      renderSkills();
    } catch (e) {
      setStatus(e.message, "bad");
    }
  };
  reader.onerror = () => setStatus(t("skills.read_fail"), "bad");
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
    ? t("skills.confirm_delete_used", { name, users: users.join("、") })
    : t("skills.confirm_delete_unused", { name });
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

    let message = t("skills.deleted", { name });
    if (cleaned.length) message += t("skills.deleted_cleaned", { users: cleaned.join("、") });
    if (skipped.length) message += t("skills.deleted_skipped", { users: skipped.join("、") });
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
      el("p", { class: "empty-list", text: t("preview.empty") })
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
              el("div", { class: "tool-call-label", text: t("preview.args") }),
              el("pre", { text: JSON.stringify(call.args, null, 2) }),
              el("div", { class: "tool-call-label", text: t("preview.result") }),
              el("pre", {
                text: call.result + (call.truncated ? t("preview.truncated") : ""),
              }),
            ]),
          ])
        );
      }
      box.append(trace);
    }
    // 流式刚开始、还没来文本时 text 为空，不渲染空气泡
    if (turn.text) {
      box.append(el("div", { class: "chat-bubble bot", text: turn.text }));
    }
  }

  // 「正在处理」气泡：只在还没开始吐字时显示，避免和流式增长的气泡重复
  const last = state.preview.turns[state.preview.turns.length - 1];
  const streaming = last && last.role === "assistant" && last.text;
  if (state.preview.busy && !streaming) {
    box.append(el("div", { class: "chat-bubble bot busy", text: t("preview.thinking") }));
  }

  box.scrollTop = box.scrollHeight;
}

async function sendPreview() {
  if (state.preview.busy || !state.current) return;
  const input = $("#preview-message");
  const message = input.value.trim();
  if (!message) return;

  if (state.dirty) {
    if (!confirm(t("preview.confirm_dirty"))) {
      return;
    }
  }

  state.preview.turns.push({ role: "user", text: message });
  state.preview.busy = true;
  input.value = "";
  renderPreview();

  const identity = $("#preview-identity").value.trim();

  // 流式（SSE）预览：逐段把回复追加到这个 assistant turn，实时打字机。
  const turn = { role: "assistant", text: "", tools: [] };
  let turnPushed = false;
  const ensureTurn = () => {
    if (!turnPushed) {
      state.preview.turns.push(turn);
      turnPushed = true;
    }
  };

  try {
    const resp = await fetch(
      `/api/bots/${encodeURIComponent(state.current)}/preview/stream`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
          "Accept-Language": getLang() === "en" ? "en" : "zh",
        },
        body: JSON.stringify({
          message,
          history: state.preview.history,
          identity: identity || undefined,
        }),
      }
    );

    // 开流前的错误（配置没过校验 / 身份不符）是普通 JSON 4xx
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        if (err && err.error) msg = err.error;
      } catch (_) { /* 保留 HTTP 状态码 */ }
      throw new Error(msg);
    }

    await readSse(resp, (event, data) => {
      if (event === null && data && typeof data.delta === "string") {
        ensureTurn();
        turn.text += data.delta;
        renderPreview();
      } else if (event === "tool") {
        ensureTurn();
        turn.tools.push(data);
        renderPreview();
      } else if (event === "error") {
        state.preview.turns.push({ role: "error", text: data.message || "出错了" });
        renderPreview();
      } else if (event === "done") {
        if (data && Array.isArray(data.history)) {
          state.preview.history = data.history;
        }
      }
    });
  } catch (e) {
    state.preview.turns.push({ role: "error", text: e.message });
  } finally {
    state.preview.busy = false;
    renderPreview();
  }
}

/** 读取一个 SSE 响应，逐事件回调 cb(event, data)。event 为 null 表示无名事件。 */
async function readSse(resp, cb) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flush = (block) => {
    const trimmed = block.trim();
    if (!trimmed) return;
    let event = null;
    let dataText = null;
    for (const line of trimmed.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataText = line.slice(5).trim();
      }
    }
    if (dataText === null) return;
    let data;
    try {
      data = JSON.parse(dataText);
    } catch (_) {
      return; // 坏分片跳过
    }
    cb(event, data);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    // SSE 事件以空行（\n\n）分隔
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      flush(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
    }
  }
  if (buffer.trim()) flush(buffer);
}

function clearPreview() {
  if (state.current) delete state.previews[state.current];
  state.preview = { history: [], turns: [], busy: false };
  renderPreview();
}

// ---------------------------------------------------------------- 用量

/** 千分位，便于看大数（token 动辄上万） */
function fmtNum(n) {
  return Number(n || 0).toLocaleString();
}

/**
 * 「用量」tab：只读查看本地落盘的用量。懒加载——进这个 tab 才拉数据，
 * 切 Agent 后 state.usage 被清空，下次进来重新拉。月份下拉切换会重新请求。
 */
function renderUsage() {
  const cards = $("#usage-cards");
  const detail = $("#usage-detail");
  const monthSel = $("#usage-month");
  if (!cards || !detail || !monthSel) return;

  // 只在当前就是 usage tab 时才拉数据，避免每次 render 都请求
  if (state.activeTab !== "usage") return;

  if (state.usage === null && !state.usageLoading) {
    loadUsage();
    return;
  }
  if (state.usageLoading || state.usage === null) {
    cards.replaceChildren(note("", t("status.reading")));
    detail.replaceChildren();
    monthSel.replaceChildren();
    return;
  }

  const u = state.usage;

  // 月份下拉
  monthSel.replaceChildren();
  for (const m of u.months) {
    monthSel.append(el("option", { value: m, text: fmtMonth(m), selected: m === u.current }));
  }
  monthSel.onchange = () => loadUsage(monthSel.value);
  monthSel.disabled = u.months.length === 0;

  // 没有任何记录
  if (!u.current || u.summary.turns === 0) {
    cards.replaceChildren();
    detail.replaceChildren(note("", t("usage.empty")));
    return;
  }

  // 指标卡
  const s = u.summary;
  const rate = s.success_rate === null ? "—" : Math.round(s.success_rate * 100) + "%";
  cards.replaceChildren(
    usageCard("usage.card_turns", fmtNum(s.turns)),
    usageCard("usage.card_total_tokens", fmtNum(s.total_tokens)),
    usageCard("usage.card_io_tokens", `${fmtNum(s.input_tokens)} / ${fmtNum(s.output_tokens)}`),
    usageCard("usage.card_tool_calls", fmtNum(s.tool_calls)),
    usageCard("usage.card_success_rate", rate),
    usageCard("usage.card_avg_elapsed", t("usage.ms", { n: fmtNum(s.avg_elapsed_ms) })),
  );

  // 明细表
  detail.replaceChildren(
    el("h3", { text: t("usage.detail_h3") }),
    el("p", { class: "hint", text: t("usage.detail_hint") }),
    usageTable(u.detail),
  );
}

function usageCard(labelKey, value) {
  return el("div", { class: "usage-card" }, [
    el("div", { class: "usage-card-value", text: value }),
    el("div", { class: "usage-card-label", text: t(labelKey) }),
  ]);
}

/** YYYYMM -> "2026-10"，下拉里好认 */
function fmtMonth(m) {
  if (!/^\d{6}$/.test(m)) return m;
  return m.slice(0, 4) + "-" + m.slice(4);
}

/** ISO8601 时间戳截到「MM-DD HH:MM」，省掉年和时区，表格里够看 */
function fmtTs(ts) {
  const s = String(ts || "");
  const m = s.match(/^\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : s;
}

function usageTable(rows) {
  const table = el("table", { class: "usage-table" });
  const cols = [
    "usage.col_ts", "usage.col_identity", "usage.col_source", "usage.col_model",
    "usage.col_in", "usage.col_out", "usage.col_total", "usage.col_tools",
    "usage.col_elapsed", "usage.col_ok", "usage.col_question",
  ];
  table.append(
    el("thead", {}, el("tr", {}, cols.map((c) => el("th", { text: t(c) }))))
  );
  const body = el("tbody");
  for (const r of rows) {
    const ok = r.success !== false;
    body.append(
      el("tr", {}, [
        el("td", { text: fmtTs(r.ts) }),
        el("td", { text: r.identity || "—" }),
        el("td", { text: r.source || "—" }),
        el("td", { text: r.model || "—" }),
        el("td", { class: "num", text: fmtNum(r.input_tokens) }),
        el("td", { class: "num", text: fmtNum(r.output_tokens) }),
        el("td", { class: "num", text: fmtNum(r.total_tokens) }),
        el("td", { class: "num", text: fmtNum(r.tool_calls) }),
        el("td", { class: "num", text: fmtNum(r.elapsed_ms) }),
        el("td", {}, el("span", {
          class: "usage-badge " + (ok ? "ok" : "bad"),
          text: ok ? t("usage.ok_yes") : t("usage.ok_no"),
        })),
        el("td", { class: "usage-q", title: r.question || "", text: r.question || "" }),
      ])
    );
  }
  table.append(body);
  return table;
}

/** 拉某个 Agent 某月的用量。month 省略时后端给最新有数据的月。 */
async function loadUsage(month) {
  if (!state.current) return;
  state.usageLoading = true;
  renderUsage();
  try {
    const q = month ? `?month=${encodeURIComponent(month)}` : "";
    state.usage = await api("GET", `/api/bots/${encodeURIComponent(state.current)}/usage${q}`);
  } catch (e) {
    state.usage = { months: [], current: null, summary: { turns: 0 }, detail: [] };
    setStatus(t("usage.load_fail", { error: e.message }), "bad");
  } finally {
    state.usageLoading = false;
    renderUsage();
  }
}

// ---------------------------------------------------------------- 数据加载

async function loadBots() {
  const data = await api("GET", "/api/bots");
  state.bots = data.bots;
  renderSidebar();
}

async function selectBot(name) {
  if (state.dirty && !confirm(t("confirm.switch_dirty"))) return;

  setStatus(t("status.reading"), "busy");
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
    state.usage = null;   // 切 Agent 后清掉用量缓存，进「用量」tab 再懒加载
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
  $("#summary").textContent = validation.summary || t("summary.none");
  if (validation.ok) {
    setStatus(t("status.validate_ok"), "ok");
    clearBanner();
  } else {
    setStatus(t("status.validate_bad"), "bad");
    showBanner(validation.error);
  }
}

function showBanner(text) {
  clearBanner();
  const banner = note("bad", t("banner.validate_fail", { error: text }));
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
  setStatus(t("status.saving"), "busy");

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
  setStatus(t("status.validating"), "busy");
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
    box.replaceChildren(note("busy", t("mcp.saving_first")));
    const ok = await save();
    if (!ok) {
      box.replaceChildren(note("bad", t("mcp.save_fail")));
      return;
    }
  }

  button.disabled = true;
  box.replaceChildren(
    note("busy", only ? t("mcp.connecting_one", { name: only }) : t("mcp.connecting_all"))
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
        box.append(note("ok", t("mcp.ok", { name: server.server, count: server.tools.length })));
      } else {
        delete state.probed[server.server];
        box.append(note("bad", t("mcp.bad", { name: server.server, error: server.error })));
      }
    }
    box.append(
      el("p", { class: "hint", text: t("mcp.identity_used", { identity: result.identity_used }) })
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
    note("busy", wait ? t("wecom.waiting") : t("wecom.connecting"))
  );

  try {
    const result = await api(
      "POST",
      `/api/bots/${encodeURIComponent(state.current)}/probe/wecom?wait=${wait ? 1 : 0}`
    );
    box.replaceChildren(note("ok", t("wecom.cred_ok")));

    if (wait && result.timed_out) {
      box.append(note("warn", t("wecom.timed_out")));
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
    list.append(el("dd", { text: value === "" ? t("wecom.kv_empty") : String(value) }));
  };
  add(t("wecom.kv_userid"), info.userid);
  add(t("wecom.kv_encrypted"), info.looks_encrypted ? t("wecom.encrypted_yes") : t("wecom.encrypted_no"));
  add(t("wecom.kv_chattype"), info.chattype === "group" ? t("wecom.chat_group") : t("wecom.chat_single"));
  add(t("wecom.kv_content"), info.content);
  add(t("wecom.kv_stripped"), info.stripped);
  add(t("wecom.kv_session"), info.session_key);
  box.append(list);

  if (info.identity_ok) {
    box.append(note("ok", t("wecom.identity_ok")));
  } else if (info.looks_encrypted) {
    box.append(note("bad", t("wecom.identity_encrypted")));
  } else {
    box.append(note("warn", t("wecom.identity_pattern", { userid: info.userid, pattern: info.pattern })));
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
    error.textContent = t("dialog.new_need_name");
    return;
  }

  try {
    const result = await api("POST", "/api/bots", { name });
    dialog.close();
    input.value = "";
    await loadBots();
    state.dirty = false;
    await selectBot(result.name);
    setStatus(t("status.created"), "busy");
  } catch (e) {
    error.textContent = e.message;
  }
}

async function deleteBot() {
  if (!state.current) return;
  const name = state.current;
  if (!confirm(t("confirm.delete_bot", { name }))) {
    return;
  }

  setStatus(t("status.deleting"), "busy");
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
    setStatus(t("status.deleted", { name }), "ok");
  } catch (e) {
    setStatus(e.message, "bad");
  }
}

async function exportBot() {
  if (!state.current) return;
  setStatus(t("status.exporting"), "busy");
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
    setStatus(t("status.exported"), "ok");
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
    error.textContent = t("dialog.import_need_text");
    return;
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (e) {
    error.textContent = t("dialog.import_bad_json", { error: e.message });
    return;
  }
  try {
    const result = await api("POST", "/api/bots/import", payload);
    dialog.close();
    $("#import-text").value = "";
    await loadBots();
    state.dirty = false;
    await selectBot(result.name);
    setStatus(t("status.imported"), "busy");
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
    $("#import-error").textContent = t("dialog.import_read_fail");
  };
  reader.readAsText(file);
}

// ---------------------------------------------------------------- 启动

/** 切换中/英：存偏好、重填静态 DOM、整页重渲染 */
function toggleLang() {
  setLang(getLang() === "zh" ? "en" : "zh");
  applyStaticI18n();
  render();
  // 校验报错横幅和配置摘要来自后端，切语言后重新校验一次，让它们也跟着切语言
  if (state.current && !state.dirty) {
    revalidate();
  }
}

async function init() {
  applyStaticI18n();
  $("#btn-lang").addEventListener("click", toggleLang);
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
    setStatus(t("status.init_fail", { error: e.message }), "bad");
  }
}

init();

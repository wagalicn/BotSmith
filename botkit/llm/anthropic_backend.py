"""
Anthropic Messages API 后端

为什么需要它：Claude 有两条访问路径，协议不同。
- 有些端点（Anthropic 官方的 OpenAI 兼容层、各类网关）说 OpenAI Chat Completions，
  用 openai / aiohttp 后端即可；
- 但 **Amazon Bedrock 上的 Claude 只提供 Anthropic Messages API**，对它发
  /chat/completions 会被拒（"does not support the '/v1/chat/completions' API"）。
  这个后端就是为这种情况准备的。

同一份实现同时覆盖两种部署：
- Bedrock：api_url = https://bedrock-mantle.<region>.api.aws/anthropic/v1
- Anthropic 官方：api_url = https://api.anthropic.com/v1
两者认证头不同（Bedrock 用 Authorization: Bearer，官方用 x-api-key），
而且**不能同时带**（Anthropic 会直接 401），所以按端点域名自动选一个，
不需要额外配置项。见 _headers / _is_bedrock_host。

对上层完全透明：产出和另外两个后端一样的 LlmResponse / StreamChunk，
engine.py 不感知协议差异。

协议差异（转换的要点）：
            OpenAI Chat Completions          Anthropic Messages
system      messages 里 role=system           顶层单独的 system 字段
max_tokens  可选                              必填
工具定义     tools[].function.parameters       tools[].input_schema
模型调工具   message.tool_calls（arguments 是   content[] 里 type=tool_use，
            JSON 字符串）                      input 是对象
回灌结果     role=tool + tool_call_id          role=user + type=tool_result
流式文本     choices[0].delta.content          content_block_delta/text_delta
流式工具参数  delta.tool_calls[].arguments 分片  input_json_delta/partial_json 分片
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import aiohttp

from botkit.config import LlmConfig
from botkit.llm.base import (
    LlmError,
    LlmResponse,
    StreamAccumulator,
    StreamChunk,
    normalize_response,
)

logger = logging.getLogger("botkit.llm.anthropic")

# 报错时回显的响应体长度上限，避免把整个响应刷进日志
_ERROR_BODY_LIMIT = 500

# Messages API 要求 max_tokens 必填，而各模型上限差异很大
# （旧 Claude 3 只有 4096，当前新模型可到 128000）。没配时用这个兜底：
# 足够覆盖日常对话，且 Claude 3.5 之后的模型都支持。要更长就配 llm.max_tokens。
_DEFAULT_MAX_TOKENS = 8192

# Anthropic 官方 API 要求的版本头。Bedrock 会忽略它，带着无害。
_ANTHROPIC_VERSION = "2023-06-01"


def _is_bedrock_host(api_url: str) -> bool:
    """
    端点是不是 Amazon Bedrock。用来决定认证头用 Bearer 还是 x-api-key
    （两者不能同时带）。按域名判断，比让用户多配一个开关省事。
    """
    host = (urlparse(api_url or "").hostname or "").lower()
    return host.endswith("amazonaws.com") or host.endswith("api.aws")


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, cfg: LlmConfig, session: aiohttp.ClientSession | None = None):
        self.cfg = cfg
        self._session = session
        self._owns_session = session is None
        if cfg.http_proxy:
            logger.info("LLM 请求将经代理转发：%s", cfg.http_proxy)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.cfg.timeout_seconds)
            )
            self._owns_session = True
        return self._session

    # -- 请求组装 ----------------------------------------------------------

    @property
    def _endpoint(self) -> str:
        """api_url 约定填到 /v1 为止，这里拼出 /messages"""
        return self.cfg.api_url.rstrip("/") + "/messages"

    def _headers(self) -> dict[str, str]:
        """
        认证头只能带一个：Anthropic 会对同时出现 authorization 和 x-api-key
        的请求直接返回 401（"must not include both ... headers"）。

        所以按端点域名选：
          - Bedrock（*.amazonaws.com / *.api.aws）→ Authorization: Bearer
          - 其余（Anthropic 官方及兼容网关）      → x-api-key（Messages API 的标准头）
        """
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        if _is_bedrock_host(self.cfg.api_url):
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        else:
            headers["x-api-key"] = self.cfg.api_key
        return headers

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        system, converted = _convert_messages(messages)
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens or _DEFAULT_MAX_TOKENS,
            "messages": converted,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = _convert_tools(tools)
        return body

    # -- 非流式 ------------------------------------------------------------

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        body = self._build_body(messages, tools, stream=False)
        session = await self._get_session()
        proxy = self.cfg.http_proxy or None
        try:
            async with session.post(
                self._endpoint, headers=self._headers(), json=body, proxy=proxy
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise LlmError(
                        f"LLM 返回 HTTP {resp.status}：{text[:_ERROR_BODY_LIMIT]}"
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    raise LlmError(
                        f"LLM 返回的不是合法 JSON（{e}）：{text[:_ERROR_BODY_LIMIT]}"
                    ) from e
        except LlmError:
            raise
        except aiohttp.ClientError as e:
            raise LlmError(f"请求 LLM 失败（{type(e).__name__}）：{e}") from e
        except TimeoutError as e:
            raise LlmError(f"请求 LLM 超时（{self.cfg.timeout_seconds:g}s）") from e

        return _parse_messages_response(data)

    # -- 流式 --------------------------------------------------------------

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[StreamChunk]:
        body = self._build_body(messages, tools, stream=True)
        acc = StreamAccumulator()
        # 流式里工具调用的参数是分片到达的，要按 content block 的下标归位
        tool_blocks: dict[int, dict[str, str]] = {}

        session = await self._get_session()
        proxy = self.cfg.http_proxy or None
        try:
            async with session.post(
                self._endpoint, headers=self._headers(), json=body, proxy=proxy
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise LlmError(
                        f"LLM 返回 HTTP {resp.status}：{text[:_ERROR_BODY_LIMIT]}"
                    )
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        # Messages API 的 SSE 还会发 "event: xxx" 行，类型在 data 里也有，
                        # 所以只看 data 行就够
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        logger.warning("流式分片不是合法 JSON，跳过：%s", payload[:200])
                        continue
                    for piece in _handle_stream_event(event, acc, tool_blocks):
                        yield piece
        except LlmError:
            raise
        except aiohttp.ClientError as e:
            raise LlmError(f"请求 LLM 失败（{type(e).__name__}）：{e}") from e
        except TimeoutError as e:
            raise LlmError(f"请求 LLM 超时（{self.cfg.timeout_seconds:g}s）") from e

        # 把分片拼好的工具调用交给累加器（文本已在事件里逐段累加过）
        for index in sorted(tool_blocks):
            block = tool_blocks[index]
            acc.add_tool_call_delta(
                index,
                call_id=block.get("id") or None,
                name=block.get("name") or None,
                arguments=block.get("input") or "",
            )

        yield StreamChunk(done=True, response=acc.finish())

    async def aclose(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()


# --------------------------------------------------------------------------
# 请求：OpenAI 形状 -> Messages API 形状
# --------------------------------------------------------------------------


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    OpenAI 的 {"type":"function","function":{name,description,parameters}}
    转成 Messages API 的 {name,description,input_schema}。
    """
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": fn.get("description") or "",
                # parameters 就是 JSON Schema，Messages API 里字段名叫 input_schema
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return out


def _convert_messages(
    messages: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """
    把 OpenAI 形状的 messages 转成 (system 文本, Messages API 的 messages)。

    两个必须处理的点：
    1. system 在 Messages API 里是顶层字段，不能留在 messages 里；
       多条 system 消息按顺序拼接。
    2. Messages API 要求 user / assistant **交替**，不能连续同角色。
       而一轮里调多个工具时，OpenAI 形状会产生多条 role=tool，转换后都是 user，
       必须合并成一条（content 数组拼起来），否则请求会被拒。
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, content: Any) -> None:
        """追加一条消息；和上一条同角色时合并 content（保证角色交替）"""
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            prev_list = prev if isinstance(prev, list) else [_text_block(prev)]
            cur_list = content if isinstance(content, list) else [_text_block(content)]
            out[-1]["content"] = prev_list + cur_list
            return
        out.append({"role": role, "content": content})

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if content:
                system_parts.append(str(content))
            continue

        if role == "tool":
            # 工具结果：Messages API 里是 user 消息里的 tool_result 块
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id") or "",
                        "content": str(content if content is not None else ""),
                    }
                ],
            )
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            # 有些模型会在调工具的同时给出思考文本，两者都要保留
            if content:
                blocks.append(_text_block(str(content)))
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        # OpenAI 存的是 JSON 字符串，Messages API 要对象
                        "input": _parse_tool_input(fn.get("arguments")),
                    }
                )
            if blocks:
                push("assistant", blocks)
            continue

        # 其余按 user 处理
        if content:
            push("user", str(content))

    return "\n\n".join(system_parts), out


def _text_block(text: Any) -> dict[str, Any]:
    return {"type": "text", "text": str(text if text is not None else "")}


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    """OpenAI 的 arguments 是 JSON 字符串，Messages API 要对象。解析失败给空对象。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("工具参数不是合法 JSON，按空对象处理：%s", str(raw)[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --------------------------------------------------------------------------
# 响应：Messages API 形状 -> 统一的 LlmResponse
# --------------------------------------------------------------------------


def _parse_messages_response(data: Any) -> LlmResponse:
    """
    从 Messages API 响应里取出文本和工具调用，产出和另外两个后端一致的 LlmResponse。

    结构：{"content":[{"type":"text","text":...},{"type":"tool_use",id,name,input}],
          "stop_reason":"tool_use"|"end_turn"|...}
    """
    if not isinstance(data, dict):
        raise LlmError(f"LLM 返回的顶层不是对象，而是 {type(data).__name__}")

    # 出错时 Messages API 返回 {"type":"error","error":{...}}
    if data.get("type") == "error" or ("error" in data and "content" not in data):
        raise LlmError(f"LLM 返回错误：{str(data.get('error') or data)[:_ERROR_BODY_LIMIT]}")

    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise LlmError(
            f"LLM 返回里没有可用的 content：{str(data)[:_ERROR_BODY_LIMIT]}"
        )

    text_parts: list[str] = []
    raw_tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            piece = block.get("text")
            if piece:
                text_parts.append(str(piece))
        elif btype == "tool_use":
            raw_tool_calls.append(
                {
                    "id": block.get("id") or "",
                    "function": {
                        "name": block.get("name") or "",
                        # normalize_response 接受对象，会自己 dumps 成字符串
                        "arguments": block.get("input") or {},
                    },
                }
            )

    content = "".join(text_parts) or None
    return normalize_response(content, raw_tool_calls)


# --------------------------------------------------------------------------
# 流式：Messages API 的 SSE 事件 -> StreamChunk
# --------------------------------------------------------------------------


def _handle_stream_event(
    event: Any,
    acc: StreamAccumulator,
    tool_blocks: dict[int, dict[str, str]],
):
    """
    处理一个流式事件，文本增量作为 StreamChunk 产出，工具调用分片存进 tool_blocks。

    关心这几类事件：
      content_block_start  工具调用块开始，带 id 和 name
      content_block_delta  text_delta=文本增量；input_json_delta=工具参数分片
    其余（message_start/message_delta/message_stop/ping）不影响产出，忽略。
    """
    if not isinstance(event, dict):
        return
    etype = event.get("type")

    if etype == "content_block_start":
        block = event.get("content_block")
        if isinstance(block, dict) and block.get("type") == "tool_use":
            index = _block_index(event)
            slot = tool_blocks.setdefault(index, {"id": "", "name": "", "input": ""})
            slot["id"] = str(block.get("id") or "")
            slot["name"] = str(block.get("name") or "")
        return

    if etype == "content_block_delta":
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return
        dtype = delta.get("type")
        if dtype == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                acc.add_text(text)
                yield StreamChunk(delta=text)
        elif dtype == "input_json_delta":
            partial = delta.get("partial_json")
            if isinstance(partial, str) and partial:
                index = _block_index(event)
                slot = tool_blocks.setdefault(
                    index, {"id": "", "name": "", "input": ""}
                )
                slot["input"] += partial
        return

    if etype == "error":
        raise LlmError(
            f"LLM 流式返回错误：{str(event.get('error') or event)[:_ERROR_BODY_LIMIT]}"
        )


def _block_index(event: dict[str, Any]) -> int:
    try:
        return int(event.get("index") or 0)
    except (TypeError, ValueError):
        return 0

"""
vLLM / OpenAI-compatible API client with streaming support.

Two public functions:

  stream_response(...)   → Generator yielding typed event dicts
  call_response(...)     → Blocking call, returns list of content blocks

Both functions accept the same arguments.  The streaming variant yields
events as they arrive so the REPL can print text tokens in real-time.
"""
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, List, Optional


# ---------------------------------------------------------------------------
# Message conversion helpers
# ---------------------------------------------------------------------------

def build_messages(conversation: list, system_prompt: str) -> List[dict]:
    """
    Convert nessocode's internal conversation format into the OpenAI
    messages array (with a system message prepended).

    Internal format:
        {"role": "user",      "content": str | list[tool_result]}
        {"role": "assistant", "content": list[text_block | tool_use_block]}
    """
    result = [{"role": "system", "content": system_prompt}]

    for msg in conversation:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
            elif isinstance(content, list) and content and content[0].get("type") == "tool_result":
                # Tool results are submitted as "tool" role messages
                for tr in content:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": tr["content"],
                    })
            else:
                result.append({"role": "user", "content": str(content)})

        elif role == "assistant":
            asst: dict = {"role": "assistant", "content": "", "tool_calls": []}
            for block in content:
                if block["type"] == "text":
                    asst["content"] += block["text"]
                elif block["type"] == "tool_use":
                    asst["tool_calls"].append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    })
            if not asst["content"]:
                asst["content"] = None
            if not asst["tool_calls"]:
                del asst["tool_calls"]
            result.append(asst)

    return result


def make_tool_defs(builtin_tools: dict, mcp_tools: Optional[list] = None) -> List[dict]:
    """Build the OpenAI tools array from built-in + MCP tool dicts."""
    result = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": defn["description"],
                "parameters": defn["parameters"],
            },
        }
        for name, defn in builtin_tools.items()
    ]
    if mcp_tools:
        result.extend(mcp_tools)
    return result


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------

def stream_response(
    api_url: str,
    model: str,
    messages: list,
    tools: list,
    max_tokens: int = 8192,
    repetition_penalty: float = 1.1,
) -> Generator[dict, None, None]:
    """
    Stream a chat completion from the vLLM server.

    Yields event dicts:
        {"type": "text_delta",  "text": str}
        {"type": "tool_start",  "id": str, "name": str}
        {"type": "tool_delta",  "id": str, "args_chunk": str}
        {"type": "done",        "blocks": list}   ← full assembled blocks
        {"type": "error",       "message": str}
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "extra_body": {"repetition_penalty": repetition_penalty},
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    # Accumulators
    text_buf = ""
    # index -> {id, name, arguments}
    tc_acc: Dict[int, Dict[str, str]] = {}

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})

                # --- text token ---
                if text := delta.get("content"):
                    text_buf += text
                    yield {"type": "text_delta", "text": text}

                # --- tool call delta ---
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tc_acc:
                        tc_acc[idx] = {"id": "", "name": "", "arguments": ""}

                    tc = tc_acc[idx]
                    if new_id := tc_delta.get("id"):
                        tc["id"] = new_id

                    fn = tc_delta.get("function", {})
                    if new_name := fn.get("name"):
                        if not tc["name"]:                       # first chunk
                            tc["name"] = new_name
                            yield {"type": "tool_start", "id": tc["id"], "name": new_name}
                        else:
                            tc["name"] += new_name

                    if args_chunk := fn.get("arguments"):
                        tc["arguments"] += args_chunk
                        yield {"type": "tool_delta", "id": tc["id"], "args_chunk": args_chunk}

        # --- build final blocks ---
        blocks: List[dict] = []
        if text_buf:
            blocks.append({"type": "text", "text": text_buf})

        for idx in sorted(tc_acc.keys()):
            tc = tc_acc[idx]
            raw_args = tc["arguments"]
            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                parsed_args = {"_raw": raw_args}
            blocks.append({
                "type":  "tool_use",
                "id":    tc["id"],
                "name":  tc["name"],
                "input": parsed_args,
            })

        yield {"type": "done", "blocks": blocks}

    except urllib.error.HTTPError as exc:
        yield {"type": "error", "message": f"HTTP {exc.code}: {exc.read().decode()[:300]}"}
    except urllib.error.URLError as exc:
        yield {"type": "error", "message": f"Connection failed: {exc.reason}"}
    except Exception as exc:
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Blocking (non-streaming) response — fallback / testing
# ---------------------------------------------------------------------------

def call_response(
    api_url: str,
    model: str,
    messages: list,
    tools: list,
    max_tokens: int = 8192,
    repetition_penalty: float = 1.1,
) -> List[dict]:
    """
    Blocking API call.  Returns assembled content blocks (same format as
    the 'blocks' field in the stream_response 'done' event).
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "extra_body": {"repetition_penalty": repetition_penalty},
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urllib.request.urlopen(req, timeout=180)
        data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed: {exc.reason}") from exc

    msg = data["choices"][0]["message"]
    blocks: List[dict] = []

    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})

    for tc in msg.get("tool_calls", []):
        raw_args = tc["function"]["arguments"]
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed = {"_raw": raw_args}
        blocks.append({
            "type":  "tool_use",
            "id":    tc["id"],
            "name":  tc["function"]["name"],
            "input": parsed,
        })

    return blocks

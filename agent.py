"""The data-analyst agent. Given the conversation so far (the Telegram
messages), it reasons with an LLM, calls run_python to fetch/compute, and
returns ONE JSON object shaped exactly as the latest message asked for.

Provider-agnostic: uses any OpenAI-compatible endpoint (OpenAI, AI Pipe,
OpenRouter, ...) via OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL.
"""
import json
import os
import re

from openai import OpenAI

from tools import TOOLS_SPEC, dispatch_tool

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "8"))
LOG_PLACEHOLDER = "LOG_URL_PLACEHOLDER"

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ["OPENAI_API_KEY"],
        )
    return _client


SYSTEM = f"""You are a meticulous data analyst answering questions over public
Indian datasets (MOSPI and similar) and any data embedded in the message.

CRITICAL RULE: You must NOT answer from memory. Before giving any final answer
you are REQUIRED to call the run_python tool at least once to fetch and/or
compute from actual data. Guessing a value without running code is a failure.
If you cannot fetch a dataset, use run_python to try alternative sources or to
reason over any data provided in the message - but you must run code first.

How you work:
- Use the run_python tool to fetch datasets by URL and to compute. Search for
  the relevant public dataset, download it, inspect it, then compute the answer.
- Data may be inline in the message, or at a public URL. No files are attached.
- Verify before you answer. Never emit a null or placeholder value - if a first
  attempt fails, try another source or approach with another run_python call.

Your FINAL message must be a single JSON object and NOTHING else - no prose,
no markdown, no code fences. It must match EXACTLY the JSON shape the latest
user message specifies (same keys, same nesting, same value types).

If - and only if - the requested shape contains a "log_url" field, set its
value to the exact string "{LOG_PLACEHOLDER}". The host replaces it with the
real URL. Never invent a URL. Do not add keys the message did not ask for.
If the message shows the shape as {{"answer": <...>, "log_url": <...>}}, put
your computed answer under "answer" in the sub-shape requested and use the
placeholder for "log_url"."""


def _extract_json(text: str):
    """Pull the last balanced {...} object out of the model's final text."""
    if not text:
        return None
    text = text.strip()
    # strip accidental code fences
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    depth, start = 0, None
    last = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = text[start : i + 1]
                try:
                    last = json.loads(chunk)
                except json.JSONDecodeError:
                    pass
    return last


def _inject_log_url(obj, log_url: str):
    """Replace the placeholder (or any top-level log_url) with the real URL."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v == LOG_PLACEHOLDER or (k == "log_url" and isinstance(v, str)):
                obj[k] = log_url
            else:
                _inject_log_url(v, log_url)
    elif isinstance(obj, list):
        for v in obj:
            _inject_log_url(v, log_url)
    return obj


def run_agent(user_messages, log, log_url: str) -> str:
    """user_messages: list[str] of the Telegram turns so far (oldest first).
    Returns a compact JSON string ready to send back to Telegram."""
    messages = [{"role": "system", "content": SYSTEM}]
    for turn in user_messages:
        messages.append({"role": "user", "content": turn})

    final_text = None
    used_tool = False
    for step in range(MAX_STEPS):
        # Force a tool call on the first step so weak models (e.g. Nano) can't
        # skip straight to guessing. Some proxies ignore tool_choice, so we
        # ALSO enforce it below by rejecting a no-tool answer on step 0.
        tool_choice = "required" if step == 0 else "auto"
        resp = client().chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice=tool_choice,
            temperature=0,
        )
        m = resp.choices[0].message
        messages.append(m.model_dump(exclude_none=True))

        if m.tool_calls:
            used_tool = True
            log("assistant_tool_calls", count=len(m.tool_calls))
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch_tool(tc.function.name, args, log)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
            continue

        # No tool call. If the model never ran code, don't accept the answer -
        # push back once and make it fetch/compute (handles providers that
        # ignore tool_choice=required).
        if not used_tool:
            log("rejected_no_tool", text=(m.content or "")[:200])
            messages.append({
                "role": "user",
                "content": (
                    "You answered without running any code. That is not allowed. "
                    "Call the run_python tool now to fetch the relevant data and "
                    "compute the answer. Do all fetching and computing in one "
                    "run_python call and print() the result. Do not guess."
                ),
            })
            continue

        final_text = m.content or ""
        log("assistant_final_text", text=final_text)
        break

    obj = _extract_json(final_text or "")
    if obj is None:
        # last-resort: a parseable object so the reply is still valid JSON
        obj = {"error": "no_answer", "log_url": log_url}
        log("format_fallback", raw=final_text)
    else:
        _inject_log_url(obj, log_url)

    reply = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    log("final_reply", reply=reply)
    return reply
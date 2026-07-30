"""Data-analyst Telegram bot.

- Receives plain-text messages (from the grader's USER account) via long
  polling - no inbound webhook needed, so it runs anywhere and is easy to test.
- For every incoming message it runs the agent and replies with EXACTLY one
  message: a single JSON object. The grader reads the reply to the last turn,
  so answering each turn with valid JSON keeps a multi-turn exchange in sync.
- Serves each run's JSONL log at /logs/<run_id>.jsonl so `log_url` is public
  and wget-able. Logs are held in memory (fine: grading downloads them while
  the process is live).

Env (see .env.example): TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL,
OPENAI_MODEL, PUBLIC_BASE_URL, PORT.
"""
import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from agent import run_agent

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
PORT = int(os.environ.get("PORT", "8000"))
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()


def make_client(timeout: float) -> httpx.AsyncClient:
    """httpx client that forces IPv4. In containers (HF Spaces, etc.) the IPv6
    route to api.telegram.org frequently hangs -> ConnectTimeout. Binding the
    local address to an IPv4 interface forces IPv4 and avoids that."""
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=3)
    return httpx.AsyncClient(timeout=timeout, transport=transport)

# run_id -> JSONL text (one JSON object per line)
LOGS: dict[str, str] = {}
# chat_id -> recent message texts, so multi-turn context is available
HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=12))


class RunLog:
    """Accumulates JSONL lines for one agent run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.lines: list[str] = []

    def __call__(self, event: str, **fields):
        rec = {"ts": round(time.time(), 3), "event": event, **fields}
        self.lines.append(json.dumps(rec, ensure_ascii=False))

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


@app.get("/")
def health():
    return {"ok": True, "runs": len(LOGS)}


@app.get("/logs/{run_id}.jsonl")
def get_log(run_id: str):
    return PlainTextResponse(
        LOGS.get(run_id, ""),
        media_type="application/x-ndjson",
    )


async def tg(method: str, **payload):
    async with make_client(40) as c:
        r = await c.post(f"{TG_API}/{method}", json=payload)
        return r.json()


async def handle_message(chat_id: int, text: str):
    run_id = uuid.uuid4().hex[:16]
    log_url = f"{PUBLIC_BASE_URL}/logs/{run_id}.jsonl"
    log = RunLog(run_id)
    log("incoming_message", chat_id=chat_id, text=text, log_url=log_url)

    HISTORY[chat_id].append(text)
    turns = list(HISTORY[chat_id])

    try:
        # agent is sync (LLM SDK + subprocess) -> keep the event loop free
        reply = await asyncio.to_thread(run_agent, turns, log, log_url)
    except Exception as e:  # noqa: BLE001
        log("agent_error", detail=f"{type(e).__name__}: {e}")
        reply = json.dumps({"error": "agent_failed", "log_url": log_url})

    LOGS[run_id] = log.text()
    # exactly ONE message back, and it is only the JSON object
    await tg("sendMessage", chat_id=chat_id, text=reply)


async def poll_loop():
    offset = None
    async with make_client(60) as c:
        # clear any webhook so long polling works - best effort, never fatal
        try:
            await c.post(f"{TG_API}/deleteWebhook", json={"drop_pending_updates": False})
        except Exception as e:  # noqa: BLE001
            print("deleteWebhook failed (continuing):", type(e).__name__, e)
        while True:
            try:
                params = {"timeout": 25, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset
                r = await c.get(f"{TG_API}/getUpdates", params=params)
                data = r.json()
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    text = msg.get("text")
                    chat = msg.get("chat", {})
                    if text and chat.get("id") is not None:
                        # process concurrently; each answers in one message
                        asyncio.create_task(handle_message(chat["id"], text))
            except Exception as e:  # noqa: BLE001
                print("poll error:", type(e).__name__, e)
                await asyncio.sleep(3)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(poll_loop())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
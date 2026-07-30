# TDS P1 — Data-Analyst Telegram Bot

An LLM agent that receives a data-analysis question over Telegram, works out the
answer (fetching public datasets like MOSPI and computing with pandas when
needed), and replies with **exactly one JSON object** in the shape the message
asks for. Each run's reasoning is saved as a public JSONL log linked via
`log_url`.

## How it works

```
Telegram (grader's user account)
        │  plain-text question
        ▼
  bot.py  ── long-polls getUpdates, 1 reply per message
        │
        ▼
  agent.py ── LLM loop (OpenAI-compatible) ──► tools.py run_python
        │        fetch dataset · compute · verify
        ▼
  single JSON object  {shape the message asked for}
  log_url ─► GET /logs/<run_id>.jsonl  (served by this same app)
```

The bot **mirrors whatever JSON shape each message specifies**. If that shape
contains a `log_url` field, it's filled with this run's real public log URL; if
it doesn't, none is added. That satisfies both the bare-answer contract in the
public grader and the `{"answer": …, "log_url": …}` wrapper in the assignment's
worked example.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

Fill `.env`:
- `TELEGRAM_BOT_TOKEN` — from `@BotFather` → `/newbot` (username must end in `bot`).
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` — any OpenAI-compatible
  provider. For IITM TDS, AI Pipe works: base `https://aipipe.org/openai/v1`.
- `PUBLIC_BASE_URL` — leave as localhost for now; set to your deploy URL later.

Run:
```bash
python bot.py          # or: uvicorn bot:app --host 0.0.0.0 --port 8000
```
It long-polls, so it works from a laptop with no public URL for *testing*.
Message your bot on Telegram; you should get back a single JSON object.

## Test with the official grader (recommended)

The grading pipeline is public. Clone it separately and point it at your bot:

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
pip install -r requirements.txt
cp .env.example .env       # add TELEGRAM_API_ID/HASH + session (see its README)
```

- Put your bot in a one-row roster `students.csv`:
  `email,github_url,telegram_bot_username` → `you@x.com,https://github.com/you/repo,your_bot`
- Add your own questions to `evals/questions.json` and set each `expected` to
  the correct answer (these are *your* practice questions — the graded set is
  separate). Note the sample question expects the **bare** `{"state": …}`; your
  bot returns exactly that for that message, so it matches.
- Run: `python generate.py --students students.csv` → `collect.py` → `grade.py`.

`grade.py` compares `json.loads(replies[-1])` to `expected`. Your final reply is
a clean single JSON object, so it parses; get the analysis right and it matches.

## Deploy (stay reachable during grading)

Any always-on host works — the process must **not sleep**, since long polling
needs to keep running and `log_url` must stay live while graders download it.

**Render** (via `render.yaml`, use a non-sleeping plan):
1. Push this repo to GitHub (public).
2. Render → New → Blueprint → pick the repo.
3. Set env vars: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and after the first
   deploy set `PUBLIC_BASE_URL` to the service's own `https://…onrender.com` URL,
   then redeploy so `log_url` points at the right host.

Railway / Fly.io / Koyeb / a small VPS work the same way: install
`requirements.txt`, run `uvicorn bot:app --host 0.0.0.0 --port $PORT`, set the
env vars, and set `PUBLIC_BASE_URL` to the public HTTPS URL.

Verify after deploy:
- `curl https://your-host/` → `{"ok": true, ...}`
- message the bot → single JSON reply
- open the `log_url` from that reply → JSONL, one object per line

> Log-hosting alternative: if you'd rather not host logs on the app, upload each
> run's JSONL to a public GCS bucket and set `log_url` to the object URL
> (`https://storage.googleapis.com/<bucket>/<run_id>.jsonl`). The app default
> keeps everything in one deploy.

## Register

Submit, comma-separated: your **public GitHub repo URL** and your **bot
username** (ends in `bot`). Example:
`https://github.com/you/tds-data-bot, your_data_bot`

## Notes & caveats

- `run_python` executes model-written code on the host — expected for a
  code-interpreter agent, but run it on a disposable host and keep only the LLM
  key in the environment.
- Logs live in memory and are served while the process runs; that's enough for
  live grading. Restarting clears old logs.
- One message back per message in — never send a "thinking…" message, or it
  desyncs the grader's turn-by-turn capture.
- Keep the model deterministic (`temperature=0`, already set) for repeatable answers.

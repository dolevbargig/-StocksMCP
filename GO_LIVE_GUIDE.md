# StocksMCP Final Go-Live Guide

## Instructions for the Agent Taking Over

Your task is to finish the live Telegram deployment and verify the complete MCP challenge. Do not rebuild the application. The MCP server, Azure orchestration, browser UI, Telegram webhook, tests, and documentation are already implemented.

Follow this runbook in order. Stop and diagnose any failed step before continuing.

Never ask the user to paste API keys, bot tokens, webhook secrets, or ngrok authentication tokens into chat. All secrets must be entered directly into the local `.env` or the relevant local CLI.

## Definition of Done

The mission is complete only when all of these are true:

- The repository is cloned on the new computer.
- The Python virtual environment is installed.
- All 48 tests pass.
- `/api/health` reports Azure, Finnhub, and Telegram configured.
- The browser UI produces a live grounded Finnhub answer.
- Uvicorn runs with one worker.
- ngrok exposes port 8000 through HTTPS.
- `TELEGRAM_PUBLIC_URL` matches the active ngrok HTTPS URL.
- The Telegram webhook is registered with the secret token.
- `telegram_webhook.py info` reports the expected URL and no last error.
- The Telegram bot answers one successful stock-research request.
- The bot answers a follow-up using its bounded chat history.
- The bot handles one invalid/non-existent company without inventing data.
- The user can explain the full Telegram -> Azure -> MCP -> Finnhub -> Telegram flow.

## Current Architecture

```text
Telegram user
  -> Telegram webhook
  -> ngrok public HTTPS URL
  -> FastAPI POST /telegram/webhook
  -> TelegramService
  -> ChatService
  -> Azure OpenAI GPT-4.1 Responses API
  -> official MCP ClientSession over stdio
  -> mcp_server.py
  -> Finnhub REST API
  -> structured MCP result
  -> Azure final answer
  -> Telegram sendMessage
```

The FastAPI process must not import or call Finnhub handlers directly. All model-selected tools must continue through the MCP stdio process.

## Phase 1: Get the Latest Source

If the repository has not been cloned:

```bash
git clone https://github.com/dolevbargig/-StocksMCP.git
cd ./-StocksMCP
```

If it is already cloned:

```bash
cd /path/to/-StocksMCP
git status --short
git pull --ff-only
```

Do not continue if `git status --short` shows unknown local source-code changes. Do not add `.env` to Git.

## Phase 2: Create the Python Environment

Python 3.12 is recommended.

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
```

The system Python does not need global pip. Always use:

```bash
.venv/bin/python -m pip
```

Never install project packages globally.

## Phase 3: Restore Local Configuration

The user transferred and decrypted `.env` on the new computer. Confirm that it exists and lock its permissions:

```bash
test -s .env && echo '.env exists' || echo '.env is missing or empty'
chmod 600 .env
```

Do not print the file contents.

Check configuration presence safely:

```bash
.venv/bin/python - <<'PY'
from config import load_settings

settings = load_settings()

print("Finnhub:", settings.finnhub_configured)
print("Azure:", settings.azure_configured)
print("Telegram:", settings.telegram_configured)
print("Allowed chats:", bool(settings.telegram_allowed_chat_ids))
print("Public URL:", bool(settings.telegram_public_url))
PY
```

Before ngrok setup, this result is expected:

```text
Finnhub: True
Azure: True
Telegram: True
Allowed chats: True
Public URL: False
```

If Finnhub, Azure, Telegram, or Allowed chats is false, fix `.env` directly on the computer before continuing. Do not ask the user to put the missing values in chat.

The expected variable names are:

```dotenv
FINNHUB_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_MODEL=gpt-4.1
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_PUBLIC_URL=
```

## Phase 4: Verify the Codebase

Compile and run the complete test suite:

```bash
.venv/bin/python -m compileall -q .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

Expected result:

```text
Ran 48 tests
OK
No broken requirements found
```

The suite includes:

- Finnhub handler validation and errors.
- Azure Responses orchestration.
- Official MCP stdio discovery and tool calls.
- FastAPI routes and safe failures.
- Telegram webhook authentication before body parsing.
- Telegram allowlisting, history, dedupe, ordering, and chunking.
- Telegram helper behavior.
- MCP subprocess secret isolation.

Do not continue to live setup if tests fail.

## Phase 5: Verify the Browser Path Locally

Start Uvicorn in terminal one:

```bash
cd /path/to/-StocksMCP
.venv/bin/python -m uvicorn app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --workers 1
```

Keep this terminal open.

In terminal two, check health:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

Expected fields:

```json
{
  "status": "ready",
  "azure_configured": true,
  "finnhub_configured": true,
  "telegram_configured": true,
  "model": "gpt-4.1"
}
```

Open the browser UI:

```text
http://127.0.0.1:8000
```

Ask:

```text
What is Nvidia's latest stock price and daily percentage change?
```

The answer should be grounded in returned Finnhub data. The collapsed tool trace should contain:

- `search_symbol`
- `get_stock_quote`

Alternatively, test through HTTP:

```bash
curl -sS http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "What is Nvidia latest stock price and daily percentage change?"
      }
    ]
  }'
```

Do not proceed to Telegram until this local request works. If this succeeds but Telegram later fails, the issue is only tunnel/webhook configuration, not Azure, MCP, or Finnhub.

## Phase 6: Install and Authenticate ngrok

Webhook mode is recommended because it matches the challenge requirements.

Install ngrok from the official source for the new Mac:

```text
https://ngrok.com/downloads/mac-os
```

If Homebrew is available, the user may use the official ngrok Homebrew instructions instead.

Verify installation:

```bash
ngrok version
```

The user must sign in to ngrok and add the account authentication token using the command shown in the ngrok dashboard. The ngrok authentication token must not be pasted into chat or committed to the repository.

## Phase 7: Start the Public Tunnel

Keep Uvicorn running in terminal one.

Start ngrok in terminal two:

```bash
ngrok http 8000
```

ngrok displays an HTTPS forwarding URL similar to:

```text
https://example-name.ngrok-free.app
```

Copy only that HTTPS base URL.

Open `.env` locally:

```bash
open -e .env
```

Set:

```dotenv
TELEGRAM_PUBLIC_URL=https://example-name.ngrok-free.app
```

Do not append `/telegram/webhook`; the helper adds that path automatically.

Save `.env` and restart Uvicorn. Settings are loaded when the FastAPI process starts, so changing `.env` without restarting is insufficient.

Recheck health:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

The health endpoint does not display the public URL, but the application should still report ready and Telegram configured.

## Phase 8: Register the Telegram Webhook

The Telegram bot token, webhook secret, approved chat ID, and public URL must already be present in `.env`.

Register the webhook:

```bash
.venv/bin/python telegram_webhook.py set
```

Verify it:

```bash
.venv/bin/python telegram_webhook.py info
```

Expected output:

```text
url=https://example-name.ngrok-free.app/telegram/webhook
pending_update_count=0
has_custom_certificate=false
has_last_error=false
```

A small nonzero pending count may clear after the bot processes messages.

The helper reads secrets from `.env`; never put a bot token in a curl URL or shell argument.

## Phase 9: Run the Telegram Demo

### Successful request

Send the bot:

```text
Research Nvidia. Include its latest quote, company profile, and important news from the last 7 days.
```

Expected model behavior:

1. Call `search_symbol` because the user supplied a company name.
2. Resolve Nvidia to NVDA.
3. Call quote, profile, and news tools with NVDA.
4. Return a grounded summary.
5. Include a research-not-investment-advice reminder.

### Follow-up request

Send:

```text
Which of those news items is most relevant to the company's core business?
```

The bot should answer using the bounded in-memory history for the approved Telegram chat.

### Graceful failure

Send:

```text
Find the ticker and current price for NotARealCompanyXYZ.
```

Expected behavior:

- The MCP tool returns `SYMBOL_NOT_FOUND` or another explicit no-data result.
- The assistant explains that no match was found.
- It does not invent a ticker, price, company, or news item.

## Phase 10: Observe the Request Flow

During the demo, keep these visible:

- Uvicorn logs in terminal one.
- ngrok request inspector or terminal output in terminal two.
- Telegram conversation.

Explain the flow:

1. Telegram sends the update to the ngrok HTTPS URL.
2. ngrok forwards it to local FastAPI.
3. FastAPI authenticates the webhook secret before parsing JSON.
4. TelegramService checks the approved chat ID and duplicate update ID.
5. ChatService sends instructions and dynamically discovered MCP tool schemas to Azure.
6. Azure chooses a tool and arguments.
7. The backend calls the tool through MCP stdio.
8. The MCP server calls Finnhub.
9. The structured Finnhub result returns to Azure.
10. Azure creates the final grounded answer.
11. TelegramService splits long answers if needed and sends them back in order.

## Phase 11: Final Project Updates

After all three Telegram tests succeed:

1. Update `README.md` project status from Building to Done.
2. Record the actual live test plan without adding secrets or private chat IDs.
3. Run the complete tests again.
4. Commit and push the documentation-only final status update.

Suggested commit message:

```text
Document successful Telegram end-to-end demo
```

Never commit `.env`, ngrok tokens, Telegram tokens, webhook secrets, or numeric private chat IDs.

## Troubleshooting

### Bot does not reply at all

Check in this order:

```bash
curl -sS http://127.0.0.1:8000/api/health
.venv/bin/python telegram_webhook.py info
```

Then verify:

1. Uvicorn is still running.
2. ngrok is still running.
3. ngrok forwards to port 8000.
4. `TELEGRAM_PUBLIC_URL` exactly matches the current ngrok HTTPS base URL.
5. Uvicorn was restarted after editing `.env`.
6. The registered webhook URL ends with `/telegram/webhook`.
7. `has_last_error=false`.
8. The sender's ID is in `TELEGRAM_ALLOWED_CHAT_IDS`.
9. The bot token is current and case-sensitive.

### Browser works but Telegram does not

Azure, MCP, and Finnhub are healthy. Focus only on:

- ngrok
- `TELEGRAM_PUBLIC_URL`
- webhook registration
- webhook secret
- approved chat ID
- Uvicorn logs

### `telegram_webhook.py info` shows an old ngrok URL

Update `.env`, restart Uvicorn, and run:

```bash
.venv/bin/python telegram_webhook.py set
.venv/bin/python telegram_webhook.py info
```

### ngrok URL changed

Free ngrok URLs may change after restart. Repeat:

1. Update `TELEGRAM_PUBLIC_URL`.
2. Restart Uvicorn.
3. Run webhook `set` again.
4. Verify webhook `info`.

### Telegram reports an active webhook while discovering chat ID

```bash
.venv/bin/python telegram_webhook.py delete --drop-pending
```

Send `/start` again, then run discovery.

### Telegram rejects the token

The token is case-sensitive. Copy the current token directly from BotFather into `.env`, without quotes or whitespace, then restart Uvicorn.

### Webhook returns 403

The registered Telegram secret does not match the application's `TELEGRAM_WEBHOOK_SECRET`. Restart Uvicorn with the correct `.env`, then register the webhook again with `telegram_webhook.py set`.

### Model or Finnhub request fails

First test `/api/chat` locally. If it fails, inspect the safe error code and verify Azure/Finnhub configuration. Do not print keys or raw provider response bodies.

## Cleanup After the Demo

Delete the webhook if the local backend will be stopped:

```bash
.venv/bin/python telegram_webhook.py delete --drop-pending
```

Stop ngrok and Uvicorn with `Ctrl+C` in their terminals.

Do not leave a webhook pointing to an inactive or reassigned ngrok URL.

## Production Limitations

This project is appropriate for a challenge demo, not a production deployment:

- Telegram history is process-local.
- Deduplication state is process-local.
- One Uvicorn worker is required.
- FastAPI background tasks are not durable.
- There is no external queue or shared state store.
- ngrok free URLs may change.

Do not expand scope into production infrastructure unless the user asks. The immediate objective is to complete and demonstrate the challenge successfully.

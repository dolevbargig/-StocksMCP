# StocksMCP Migration Handoff for the Next Agent

## Read This First

This document is the complete continuation brief for an agent taking over the project on another computer.

The project is functionally implemented and well tested. The remaining work is operational setup: transfer the repository, recreate local secrets, start the backend, expose it through a public HTTPS URL, register the Telegram webhook, and perform the final live Telegram demo.

Do not rebuild the MCP server, chat service, browser UI, or Telegram webhook. Those parts already exist.

## Current Status

### Completed

- Finnhub integration with four MCP tools.
- Official MCP Python SDK server over stdio.
- Azure OpenAI GPT-4.1 Responses API orchestration.
- Same-origin FastAPI browser chat application.
- Authenticated Telegram webhook integration.
- Telegram approved-chat allowlist, update deduplication, per-chat history, same-chat ordering, global concurrency limit, typing action, safe errors, and 4096-character message splitting.
- Credential-safe Telegram webhook administration helper.
- Unit and protocol tests.
- Live local Azure -> MCP -> Finnhub test.

### Verified

- The automated suite has 48 passing tests.
- A real MCP subprocess is covered by tests.
- The local live research request successfully invoked:
  1. `search_symbol` for Nvidia.
  2. `get_stock_quote` for NVDA.
  3. Finnhub data retrieval.
  4. A grounded Azure-generated final answer.
- Telegram token authentication and chat-ID discovery succeeded on the original computer.
- Source scans found no embedded credentials.

### Not Yet Completed

- No public HTTPS tunnel or cloud deployment exists.
- No Telegram webhook has been registered.
- The FastAPI process is not currently running.
- `TELEGRAM_PUBLIC_URL` is not configured.
- ngrok is not installed on the original computer.
- The repository has no GitHub remote and has not been pushed.

Because Telegram cannot call `127.0.0.1`, the bot currently receives no webhook events and therefore does not reply.

## Required Runtime Flow

```text
Telegram user
  -> Telegram webhook delivery
  -> public HTTPS URL (ngrok or deployed service)
  -> FastAPI POST /telegram/webhook
  -> ChatService
  -> Azure OpenAI Responses API (GPT-4.1)
  -> official MCP ClientSession over stdio
  -> mcp_server.py
  -> Finnhub REST API
  -> structured MCP result
  -> Azure final answer
  -> Telegram sendMessage
```

The FastAPI/chat layer must never import or call Finnhub handlers directly. Tool execution must continue through the official MCP stdio boundary.

## Repository and Transfer State

On the original computer:

- Repository path: `/Users/bargigdolev/StocksMCP`
- Branch: `main`
- Initial commit: `72412cb Build Finnhub MCP stock research assistant`
- Git remote: none
- Working tree before this handoff: clean

Ignored local-only files:

- `.env`
- `.venv/`
- `.llms/`
- `PROJECT_HANDOFF.md`

Never add `.env` to Git or an archive intended for sharing.

## Architecture and Important Files

### MCP definitions and Finnhub

- `tools_definitions.py`: JSON Schema definitions for the four MCP tools.
- `tool_handlers.py`: input validation, Finnhub HTTP requests, output normalization, and structured error envelopes.
- `system_prompt.py`: assistant behavior, search-first resolution, chaining rules, no-hallucination policy, and answer formatting.
- `mcp_server.py`: official `MCPServer` registration and stdio transport.

### Model and MCP client

- `azure_responses.py`: exact Azure Responses endpoint transport using the `api-key` header.
- `mcp_client.py`: request-scoped MCP subprocess and `ClientSession`; forwards only an allowlisted process environment plus `FINNHUB_API_KEY`.
- `chat_service.py`: Azure function-call loop, MCP schema translation, JSON Schema validation, `call_id` preservation, partial failures, trace output, and bounded tool rounds.

### HTTP and browser

- `app.py`: FastAPI app factory, `/`, `/api/health`, `/api/chat`, and `/telegram/webhook`.
- `static/index.html`, `static/app.js`, `static/styles.css`: browser chat UI.
- `config.py`: immutable environment-backed settings.

### Telegram

- `telegram_service.py`: Telegram models, Bot API client, allowlisting, history, deduplication, locks, concurrency, safe failure replies, and answer chunking.
- `telegram_webhook.py`: CLI for `discover-chat-id`, `set`, `info`, and `delete`.

### Tests and documentation

- `tests/`: 48 unit, HTTP, orchestration, Telegram, and MCP protocol tests.
- `README.md`: challenge brief, architecture, browser setup, Telegram setup, demos, and limitations.
- `.env.example`: variable names and safe placeholders only.

## MCP Tools

1. `search_symbol(query: str)`
   - Calls Finnhub `/api/v1/search`.
   - Resolves company names to symbols.
   - Returns at most 8 candidates with truncation metadata.

2. `get_stock_quote(symbol: str)`
   - Calls Finnhub `/api/v1/quote`.
   - Returns current price, absolute and percentage change, high, low, open, previous close, and timestamp.

3. `get_company_profile(symbol: str)`
   - Calls Finnhub `/api/v1/stock/profile2`.
   - Returns company identity, exchange, industry, country, currency, market capitalization, and related fields.

4. `get_company_news(symbol: str, days_back: int = 7)`
   - Calls Finnhub `/api/v1/company-news`.
   - Returns at most 10 recent articles, newest first, with truncation metadata.

Every tool returns either:

```json
{"ok": true, "tool": "tool_name", "data": {}}
```

or:

```json
{
  "ok": false,
  "tool": "tool_name",
  "error": {
    "code": "ERROR_CODE",
    "message": "Actionable safe message",
    "retryable": false
  }
}
```

## Security Invariants

The next agent must preserve these rules:

1. Never hardcode Finnhub, Azure, Telegram, or webhook credentials.
2. Never put credentials in frontend JavaScript, README files, Git commits, command arguments, or chat messages.
3. Keep `.env` ignored and owner-readable only.
4. Authenticate Telegram webhook requests using `X-Telegram-Bot-Api-Secret-Token` before parsing JSON.
5. Keep `TELEGRAM_ALLOWED_CHAT_IDS` restricted to approved IDs.
6. Do not return raw provider response bodies or stack traces to users.
7. Do not let MCP child processes inherit Azure or Telegram secrets.
8. Use one Uvicorn worker while Telegram history and deduplication remain in memory.

## New Computer Setup

### 1. Transfer the source

Preferred option: push this repository to an empty GitHub repository from the original computer, then clone it on the new computer.

Alternative: copy the credential-free source archive created beside the repository and extract it on the new computer.

### 2. Create the Python environment

From the new checkout:

```bash
cd /path/to/StocksMCP
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
```

If the system `python3` has no global pip, that is fine. All installation must use `.venv/bin/python -m pip`.

### 3. Run tests before adding secrets

```bash
.venv/bin/python -m compileall -q .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

Expected result: 48 tests pass.

### 4. Recreate local configuration

```bash
cp .env.example .env
chmod 600 .env
```

Populate these values directly on the new computer:

```dotenv
FINNHUB_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=https://YOUR_RESOURCE.openai.azure.com/openai/v1/responses?api-version=preview
AZURE_OPENAI_MODEL=gpt-4.1
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_PUBLIC_URL=
```

The existing `.env` on the original computer is intentionally not tracked. Transfer credentials through an approved password manager or recreate them. Do not send them to the next agent through chat.

To generate a new webhook secret:

```bash
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

### 5. Confirm the local application

Start the backend:

```bash
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
```

Check health in another terminal:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

Open the browser UI at:

```text
http://127.0.0.1:8000
```

Test the local research path before Telegram:

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

A successful result should contain an answer plus a trace containing `search_symbol` and `get_stock_quote`.

## Telegram Webhook Activation with ngrok

Webhook mode is the recommended mode for the challenge because it matches the assignment architecture.

### 1. Install and authenticate ngrok

Install ngrok using the official installer for the new computer. Sign in and configure the account authentication token using the command shown in the ngrok dashboard.

Do not place the ngrok authentication token in this repository.

Verify:

```bash
ngrok version
```

### 2. Start the backend

Terminal one:

```bash
cd /path/to/StocksMCP
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
```

Keep this process running.

### 3. Start the tunnel

Terminal two:

```bash
ngrok http 8000
```

Copy the HTTPS forwarding base URL, for example:

```text
https://example-name.ngrok-free.app
```

Set that base URL as `TELEGRAM_PUBLIC_URL` in `.env`. Do not append `/telegram/webhook`; the helper appends it.

Restart Uvicorn after changing `.env` because settings are loaded at process startup.

### 4. Discover the approved chat ID if needed

Before a webhook is registered, send `/start` to the bot and run:

```bash
.venv/bin/python telegram_webhook.py discover-chat-id
```

Copy only the numeric ID into `TELEGRAM_ALLOWED_CHAT_IDS` in `.env`, then restart Uvicorn.

If Telegram reports an active webhook during discovery:

```bash
.venv/bin/python telegram_webhook.py delete --drop-pending
```

Send `/start` again and retry discovery.

### 5. Register the webhook

```bash
.venv/bin/python telegram_webhook.py set
.venv/bin/python telegram_webhook.py info
```

Expected status:

- URL ends in `/telegram/webhook`.
- `pending_update_count` is zero or small.
- `has_last_error=false`.

### 6. Test Telegram

Successful request:

```text
Research Nvidia. Include its latest quote, company profile, and important news from the last 7 days.
```

Follow-up:

```text
Which of those news items is most relevant to the company's core business?
```

Graceful-error request:

```text
Find the ticker and current price for NotARealCompanyXYZ.
```

The bot must not invent a symbol or price when no data exists.

### 7. Cleanup

```bash
.venv/bin/python telegram_webhook.py delete --drop-pending
```

A changed ngrok URL requires:

1. Update `TELEGRAM_PUBLIC_URL`.
2. Restart Uvicorn.
3. Run `telegram_webhook.py set` again.

## Why the Telegram Bot Is Silent Right Now

The current bot is silent because all of these are true on the original computer:

- Uvicorn is not running on port 8000.
- ngrok is not installed.
- `TELEGRAM_PUBLIC_URL` is empty.
- No Telegram webhook is registered.

The bot code and local model/MCP/Finnhub path are working. The missing part is the public transport from Telegram to the local FastAPI route.

## Polling Alternative

Polling can replace ngrok, but polling is not implemented in this repository.

Polling would change the entry path to:

```text
local polling process -> Telegram getUpdates -> ChatService -> MCP -> Finnhub -> sendMessage
```

Use polling only if the new computer cannot run ngrok or deploy a public HTTPS service. Webhook mode is preferable for the challenge because the assignment explicitly describes Telegram webhook plus ngrok.

If implementing polling, reuse `TelegramService` and `ChatService`; do not duplicate Azure/MCP/Finnhub orchestration. Ensure an existing webhook is deleted before calling `getUpdates`.

## GitHub Publication State

The repository was initialized locally and committed, but no remote exists.

To publish from the original computer:

1. Create an empty public GitHub repository without README, `.gitignore`, or license.
2. Copy its SSH URL.
3. Run:

```bash
cd /Users/bargigdolev/StocksMCP
git remote add origin git@github.com:YOUR_USERNAME/StocksMCP.git
git push -u origin main
```

The local SSH key must already be registered with GitHub.

Before pushing, confirm:

```bash
git status --short
git check-ignore -v .env .venv PROJECT_HANDOFF.md .llms
```

Never force-add ignored local files.

## Troubleshooting Map

### Bot sends no reply

Check in this order:

1. Uvicorn is running on port 8000.
2. ngrok is running and forwards to port 8000.
3. `TELEGRAM_PUBLIC_URL` matches the current ngrok HTTPS URL.
4. Uvicorn was restarted after `.env` changes.
5. `telegram_webhook.py info` shows the correct webhook URL.
6. `has_last_error` is false.
7. The sender's numeric ID appears in `TELEGRAM_ALLOWED_CHAT_IDS`.
8. Azure and Finnhub values are present.
9. Local `/api/chat` works independently.

### `discover-chat-id` reports no IDs

- Send `/start` to the bot.
- Confirm no webhook is active.
- Retry `discover-chat-id`.

### `discover-chat-id` reports an active webhook

```bash
.venv/bin/python telegram_webhook.py delete --drop-pending
```

Send `/start` again and retry.

### Telegram rejects the token

- Tokens are case-sensitive.
- Copy the current token directly from BotFather into `.env`.
- Do not add quotes or whitespace.

### `python3 -m pip` fails

Use the virtual environment interpreter:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### Browser works but Telegram does not

That confirms Azure, MCP, and Finnhub are healthy. Focus only on Uvicorn, tunnel, webhook registration, webhook secret, and allowlist.

## Demo Limitations

- Telegram history and dedupe state are process-local and disappear on restart.
- One Uvicorn worker is required.
- FastAPI background tasks are not durable.
- ngrok URLs commonly change between sessions.
- This is challenge/demo infrastructure, not a production deployment.

## Instructions to the Next Agent

1. Inspect Git status and run tests before changing code.
2. Do not redesign completed components.
3. Help the user transfer or clone the repository first.
4. Recreate `.env` locally without asking the user to paste secrets into chat.
5. Validate `/api/chat` locally.
6. Install/authenticate ngrok with the user.
7. Set `TELEGRAM_PUBLIC_URL`, restart Uvicorn, and register the webhook.
8. Verify `telegram_webhook.py info`.
9. Run one successful Telegram query, one follow-up, and one graceful failure.
10. Update README project status from Building to Done only after the live Telegram test succeeds.

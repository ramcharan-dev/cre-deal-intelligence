# CRE AI Deal Intelligence (POC)

Phase 1 foundation: monorepo, configuration, database + migrations, health APIs.

| Layer    | Stack                                                           |
| -------- | --------------------------------------------------------------- |
| Frontend | Next.js 16 · TypeScript · Tailwind v4 · shadcn/ui               |
| Backend  | FastAPI · Python 3.12 · SQLAlchemy 2 (async, psycopg 3) · Alembic |
| Database | PostgreSQL 18 + pgvector                                        |
| AI       | Claude Opus 5 · Voyage `voyage-4-large` · Cohere `rerank-v4.0-pro` (configured, not yet called) |

```
.
├── docker-compose.yml     # db, backend, frontend
├── .env.example           # copy to .env
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS, routers
│   │   ├── core/config.py     # pydantic-settings (all env config)
│   │   ├── db/                # engine/session, declarative Base
│   │   ├── api/routes/        # health.py
│   │   └── services/          # ai_clients.py (lazy Anthropic/Voyage/Cohere clients)
│   ├── alembic/               # migrations (0001 enables pgvector)
│   └── tests/
└── frontend/
    └── src/
        ├── app/               # status page, /api/health route
        ├── components/ui/     # shadcn components
        └── lib/api.ts         # server-side backend client
```

## Run

```bash
cp .env.example .env        # set POSTGRES_PASSWORD; API keys optional for now
docker compose up -d --build
```

Host ports come from `.env` (`FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT`; defaults 3001/8001/5432;
containers still listen on 3000/8000 internally). The frontend is at `http://localhost:3001` and Swagger at
`http://localhost:8001/docs`. If you change `FRONTEND_PORT`, update `CORS_ORIGINS` and `GOOGLE_REDIRECT_URI` to match.
Running the frontend on the host (`cd frontend && npm run dev`) also serves it on port 3001.

| URL                                     | What                                             |
| --------------------------------------- | ------------------------------------------------ |
| `http://localhost:$FRONTEND_PORT`       | Status page (frontend → backend → database)      |
| `http://localhost:$FRONTEND_PORT/api/health` | Frontend health + backend readiness passthrough |
| `http://localhost:$BACKEND_PORT/api/health`       | Backend liveness (no dependencies)      |
| `http://localhost:$BACKEND_PORT/api/health/ready` | DB connectivity, pgvector version, migration revision, provider key status; 503 if the DB is unhealthy |
| `http://localhost:$BACKEND_PORT/docs`   | OpenAPI docs                                     |

The backend runs `alembic upgrade head` on start. Backend and frontend hot-reload from bind mounts.

## Common tasks

```bash
# migrations
docker compose exec backend alembic revision --autogenerate -m "add deals"
docker compose exec backend alembic upgrade head

# tests + lint (tests need the running, migrated database)
docker compose exec backend pytest
docker compose exec backend ruff check . && docker compose exec backend ruff format --check .
cd frontend && npm run lint && npx tsc --noEmit

# add shadcn components
cd frontend && npx shadcn@latest add <component>

# psql
docker compose exec db psql -U cre -d cre_deal_intel
```

New SQLAlchemy models should subclass `app.db.base.Base` and be imported in `backend/alembic/env.py` so autogenerate sees them.
Embedding columns use `pgvector.sqlalchemy.Vector(settings.embedding_dimensions)` (1024 for `voyage-4-large` by default).

## Phase 2: Email intelligence

`/emails` in the frontend (or `POST /api/emails` with a multipart `file`) takes a raw `.eml`:

1. **Parse** (`services/email_parser.py`, stdlib): subject, sender, to/cc, date, Message-ID/thread headers, text body (HTML fallback).
2. **Extract** (`extraction/claude.py`): Claude (`ANTHROPIC_MODEL`, adaptive thinking, `ANTHROPIC_EFFORT`) fills the
   `EmailExtraction` Pydantic schema via structured outputs. Every value comes with a verbatim `source_text`.
3. **Validate** (`extraction/validation.py`): each value is coerced to its field type and bounds, and its
   `source_text` must appear in the email. Anything else is dropped and returned as an `issue`.
4. **Match** (`extraction/matching.py`): email thread → exact address → Claude's pick (high/medium confidence)
   → exact property name → new deal.
5. **Persist** (`services/email_ingestion.py`): create/update `deals`, `lenders`, `quotes` (one per deal + lender +
   option). Every value is also written to `extracted_values` with `source_email_id` and `source_text`. A value from
   an older email never overwrites one from a newer email. Re-uploading the same email returns the stored result.

**Fields extracted** are defined in one place, `backend/app/extraction/fields.py`. To add a field, add a `FieldSpec`,
add the matching column to `Deal`/`Quote` in `app/models`, and generate a migration.

Other endpoints: `GET /api/emails`, `GET /api/emails/{id}`, `GET /api/deals`, `GET /api/deals/{id}`.
Tests use a separate `<POSTGRES_DB>_test` database and a fake extractor, so no API calls are made.

### Running against the real APIs

- Set `ANTHROPIC_API_KEY` in `.env`. If the key is not scoped to a single workspace, the API requires
  `ANTHROPIC_WORKSPACE_ID` as well (sent as the `anthropic-workspace-id` header). Restart the backend after
  changing `.env` (`docker compose up -d backend`, since env files are read when the container is created).
- `VOYAGE_API_KEY` / `COHERE_API_KEY` are not called by Phase 2; they are reserved for retrieval.
- Sample emails for a full thread are in `samples/emails/` (submission → lender reply → quote summary).
- Swagger UI: `http://localhost:$BACKEND_PORT/docs` (ReDoc at `/redoc`, spec at `/openapi.json`).

**Errors**: every non-2xx response is `{"detail": {"code", "message", "retryable", "email_id", "request_id"}}`.
Claude failures map to `not_configured`/`auth_failed`/`permission_denied`/`billing` (503), `invalid_request` (502),
`rate_limited` (429), `overloaded` (503), `upstream_error`/`connection_error` (502), `timeout` (504) and
`refused`/`truncated`/`invalid_output` (422). The failed email is kept, and re-uploading it retries.

**Logging**: one line per request (`METHOD /path -> status in Nms (req/resp bytes)`) tagged with the
`X-Request-ID`, and one line per Claude call (model, effort, stop reason, token usage, Anthropic request id).
Bodies, query strings and email content are never logged, SQL parameters are hidden, configured secrets are
redacted from every log line, and unhandled exceptions log only their type and stack frames.

## Phase 3: Gmail integration

`/integrations/gmail` connects a Gmail mailbox with Google OAuth 2.0 (scope `gmail.readonly` only) and feeds its
messages into the same pipeline as uploads.

1. **Connect** (`GET /api/gmail/oauth/start` → Google → `GET /api/gmail/oauth/callback`): web-server flow with PKCE
   and offline access. `state` + PKCE verifier live in an encrypted, HttpOnly, 10-minute cookie. Only the refresh
   token is stored, Fernet-encrypted with `TOKEN_ENCRYPTION_KEY`; access tokens are minted per request and never
   stored or logged.
2. **Sync** (`POST /api/gmail/sync`): lists the newest messages in a date window (default 30 days; chats, Promotions
   and Social excluded), skips Gmail ids already stored (`gmail_messages` is unique on account + Gmail id), fetches
   new ones as raw RFC 822, parses them with `email_parser`, and scores CRE relevance/category with keyword rules
   (`services/gmail_relevance.py`, no AI call). Relevant messages are normalized into `emails` (status `received`).
3. **Process** (`POST /api/gmail/messages/{id}/process`): passes the stored raw source to `ingest_email`, unchanged.
4. **Disconnect** (`DELETE /api/gmail/connection`): revokes the token at Google and deletes the credentials and
   message index; emails and extracted data are kept.

Other endpoints: `GET /api/gmail/connection`, `GET /api/gmail/messages`, `GET /api/gmail/messages/{id}`.
The browser reaches all of them through the frontend's `/api/gmail/*` proxy, which keeps the OAuth cookie on the
frontend origin.

**Setup**

1. In Google Cloud, enable the **Gmail API** and configure the OAuth consent screen. While the app is in
   *Testing*, add your Gmail address as a test user. Google expires refresh tokens for Testing apps after
   7 days, after which the page asks you to reconnect.
2. Create an OAuth client of type **Web application** with the authorized redirect URI
   `http://localhost:3001/api/gmail/oauth/callback` (the `GOOGLE_REDIRECT_URI` in `.env.example`). If you change
   `FRONTEND_PORT`, update both the OAuth client and `GOOGLE_REDIRECT_URI`.
3. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `TOKEN_ENCRYPTION_KEY` in `.env` (see `.env.example`), then
   `docker compose up -d backend`.

**Local extraction with Ollama**: set `EXTRACTION_PROVIDER=ollama` (and optionally `OLLAMA_MODEL`, default
`qwen2.5:7b`). `extraction/ollama.py` sends the same prompt and `EmailExtraction` schema to Ollama's `/api/chat`
with structured output; validation, matching and persistence are unchanged. Expect 1-2 minutes per email on
a laptop. `OLLAMA_NUM_CTX` (default 16384) must fit the prompt, or Ollama silently truncates it.

Tests fake Google with an `httpx.MockTransport` (`tests/test_gmail.py`), so no Google credentials are needed.
## Deal dashboard

`/deals` lists one row per transaction (status, borrower, quotes, best terms). `/deals/{id}` (`GET /api/deals/{id}`)
is the transaction dashboard: status, property details, borrower/client, lenders approached (responded, or sent the
package and yet to reply), a lender quote comparison (loan amount, rate, LTV, tenure, fees, security, conditions,
prepayment, quote date, validity), the email timeline, documents (attachment names), activities and meetings
mentioned in emails, pending actions with the responsible person and due date, and the deal summary. Everything is
derived from stored emails and validated values (`services/deal_dashboard.py`), and each item links to its source
email and verbatim text. Open dashboards refresh on focus and every 20 s, so emails processed from Gmail show up
without a reload.

Duplicate deals are prevented by matching on the email thread, address, the model's pick, or the property/deal name,
and by resolving and saving each email's deal under a Postgres advisory lock, so two emails about the same new deal
processed at once create one deal.

## POC demo (no AI model required)

The POC runs end to end with the **demo extraction provider**: `DemoExtractor` (`app/extraction/demo.py`) replays
hand-written extractions for the bundled demo emails in `backend/demo_data/` (15 emails, 5 deals, 6 lenders,
9 quotes, 2 historical 2025 deals, 1 non-deal newsletter, 1 email with an attached term sheet). Its output goes
through the normal validation → matching → persistence pipeline, so every value still needs verbatim source text.

Providers sit behind one interface: business logic uses `app.extraction.base.Extractor`, and
`app.extraction.providers` picks the implementation from `LLM_PROVIDER` (`demo`, `fallback`, `gemini`,
`groq`, `local`, `ollama`, and `claude`/`anthropic` are supported). Uploads and Gmail-synced emails use the same
selection. When `LLM_PROVIDER` is empty, `claude` or `anthropic`, `EXTRACTION_PROVIDER` (`claude`/`ollama`) decides. Copilot has multi-provider fallback (Gemini → Groq → Deterministic).

```bash
# 1. In .env set LLM_PROVIDER=demo (needed only for uploads through the UI/API), then:
docker compose up -d --build
# 2. Load the demo emails (always uses the demo provider; safe to re-run)
docker compose exec backend python -m app.scripts.seed_demo
```

Demo walkthrough:

1. **Email → identification → extraction:** `/emails` lists the processed emails. Opening one shows its type
   (deal submission / lender quote / update / other), the matched deal and how it was matched (same thread,
   property address, property name), and each extracted value with its verbatim source text.
2. **Dashboard:** `/deals` shows active vs. historical deals, quote/lender counts, lowest fixed rate and max proceeds.
3. **Quote comparison:** *The Lofts at Riverbend* has 4 competing lenders (Beacon term sheet, Northmark, Summit
   floating, Granite declined). Best comparable values are highlighted (fixed rates vs. fixed only).
4. **Sources:** click any value (comparison, summary, field tables) to open the email with that text highlighted.
5. **Copilot:** `/copilot` or the box on a deal page: "Compare lender quotes for this deal", "Which lender has the
   lowest fixed rate?", "Which lender declined?", "What are the pending actions?", "What lenders are associated
   with this deal?", or search history (e.g. "When did Cedar Grove close?"). Every item links to its source email.

To show a live upload, start from an empty database and upload `01_deal_submission.eml` and then
`02_lender_quote_reply.eml` (from `backend/demo_data/emails/`) on `/emails`: the first creates a new deal, the second
is a lender quote matched to it by email thread. Then run the seed script to load the rest; emails already uploaded
are skipped. Re-uploading an already processed email returns the stored result. The demo provider only recognises
the bundled demo emails; any other email returns a clear `not_configured` error until a real model provider is
configured.

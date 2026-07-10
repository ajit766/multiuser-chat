# Multi-User Chat — Project Notes

Portfolio microservices project: WhatsApp-style 1:1 chat. Beginner learning
microservices, building and deploying this end to end. Full plan history is
in `/Users/ajitg/.claude/plans/vast-gliding-salamander.md`.

## Status

**Phase 1 (local build) — code complete, user is doing final manual verification.**
**Phase 2 (CI/CD + EC2 deploy) — not started, plan below.**

Do not start Phase 2 until the user confirms Phase 1 is verified.

## Architecture

Six FastAPI services + Nginx, all Dockerized, one `docker-compose.yml` for local dev:

- **user-service** (`services/user-service`) — owns `users_db` (Postgres). Register, list users, internal credential lookup for Auth Service. bcrypt password hashing.
- **auth-service** (`services/auth-service`) — no DB of its own. Calls user-service's internal endpoint to verify credentials, issues JWTs (PyJWT, HS256, shared `JWT_SECRET`).
- **message-service** (`services/message-service`) — owns `messages_db` (Postgres). Send/fetch messages, publishes `message.created` to RabbitMQ after a successful DB write.
- **presence-service** (`services/presence-service`) — Redis-backed online/offline with TTL heartbeat. Internal-only (no public routes).
- **gateway-service** (`services/gateway-service`) — owns live WebSocket connections (in-memory `{user_id: socket}` map — single instance only in v1, see Known Limitations). JWT passed as `?token=` query param (browsers can't set WS headers). Internal `/internal/push` endpoint for Delivery Service.
- **delivery-service** (`services/delivery-service`) — pure worker, no HTTP server. Consumes `message.created` from RabbitMQ, checks presence, pushes via gateway-service, marks messages `DELIVERED` via message-service.
- **nginx** (`nginx/`) — single public entry point. Multi-stage Docker build: builds the React frontend (`frontend/`) and serves it as static files, reverse-proxies `/auth`, `/users`, `/messages` (REST) and `/ws` (WebSocket) to the backend services. `/internal/*` routes are never proxied here — internal-only, protected by `X-Internal-Api-Key` as defense in depth.
- **frontend** (`frontend/`) — React + Vite + TypeScript. `npm run dev` proxies to the dockerized backend at `localhost:8000` (see `vite.config.ts`) for local iteration without CORS.

Postgres runs as one container with two databases (`users_db`, `messages_db`) — real ownership boundary at the DB level, low ops for a single EC2 box.

## Running locally

```
cp .env.example .env   # already done in this checkout
docker compose up -d --build
```

Then open **http://localhost:8000**. Direct per-service ports for debugging: user-service `:8001`, auth-service `:8002`, message-service `:8003`, presence-service `:8004`, gateway-service `:8005`. RabbitMQ management UI at `:15672` (user/pass from `.env`, default `chatapp`/`chatapp`).

Run a service's tests: `docker run --rm -v "$(pwd)":/app -w /app python:3.12-slim bash -c "pip install -q -r requirements-dev.txt && pytest -q"` from inside `services/<name>/`.

## Design decisions worth remembering

- **JWT contract**: `{"sub": "<user_id>", "username": "...", "iat": ..., "exp": ...}`, HS256, shared `JWT_SECRET` env var across services. Every service that needs identity decodes locally rather than calling Auth Service per-request.
- **Internal endpoints** (`/internal/*`): never routed through Nginx, plus require `X-Internal-Api-Key` header as defense in depth.
- **RabbitMQ** over Kafka deliberately — simpler single-box ops, still a real broker. Simplest pattern: default exchange, durable queue named `message.created`, no exchange/binding ceremony.
- **Message durability**: Postgres write happens before the RabbitMQ publish; a failed publish just delays real-time delivery (recipient still gets it via history fetch), never loses the message.
- **Delivery = pushed to a live socket**, not a client-side read receipt. Simpler than real WhatsApp semantics, documented as an intentional v1 simplification.
- **Message status state machine**: `SENT` (persisted) → `PENDING` (delivery-service tried once at `message.created` time, recipient was offline or push failed) → `DELIVERED`. Two independent triggers can cause the PENDING→DELIVERED transition: (1) **primary** — `gateway-service` calls `message-service`'s `POST /internal/messages/mark-delivered-for-user` the instant a user's WebSocket connects, bulk-catching-up *every* pending conversation at once (not just the one they open first) and pushing `status_update` to each original sender directly via its own in-memory connection map (no extra HTTP hop); (2) **fallback** — `message-service`'s `GET /messages/{other_user_id}` also self-heals status for that one conversation, in case the connect-time catch-up call failed. This design (WS-connect-triggered bulk catch-up) was user-proposed after finding the original REST-fetch-only version left other pending conversations stale.

## Known limitations (intentional v1 simplifications — mention in the portfolio README)

- Gateway Service's connection map is in-memory and single-instance. Scaling to multiple gateway instances needs shared state (Redis pub/sub) so a push for a user connected to instance B reaches them via instance A.
- No dead-letter queue for delivery-service; a poison message is logged and ack'd (dropped), not retried.
- Logout is client-side only (stateless JWT); no server-side revocation blocklist.
- No message pagination cursor UI, no group chat, no read receipts beyond delivery — matches PRD scope.
- **No frontend WebSocket auto-reconnect** (`frontend/src/ws/useChatSocket.ts`). If the socket drops (mobile tab suspension, laptop sleep, network change), the user must reload the page. Gateway Service correctly detects the disconnect server-side and marks them offline either way. User is aware, asked to defer this fix — pick it up if not done yet when resuming.

## Fixed after initial Phase 1 verification (2026-07-10)

- Logout link was invisible (teal text on teal `.sidebar-header` background) — fixed in `frontend/src/styles.css`.
- Tick contrast was poor and, more importantly, **the sender's own tick never updated to double-tick** — Delivery Service only pushed the delivered message to the recipient, never told the sender. Fixed: `delivery-service/app/worker.py` now also pushes a `status_update` event to `from_user_id` after marking delivered; frontend (`useChatSocket.ts`, `ChatApp.tsx`) now dispatches on event `type` (`message` vs `status_update`) and patches the matching message's status in place.
- **Messages sent to an offline recipient stayed `SENT` forever**, even after the recipient came online and opened the conversation. Root cause: `delivery-service` only attempts delivery once, at the moment `message.created` is consumed — if the recipient was offline, it just leaves the message and moves on; nothing re-triggers later. First fix attempt (REST-fetch-triggered, scoped to one conversation) only caught up whichever chat the recipient happened to open first, leaving other pending senders stale. **Superseded** by the state-machine design described above (user-proposed) — see "Message status state machine" in Design decisions. Along the way, caught `httpx` missing from `message-service/requirements.txt` (only in `requirements-dev.txt`) when a new runtime import was added — services need their real deps in the prod requirements file even if tests already exercise the import path.

## A bug we hit and fixed (worth remembering)

Nginx resolves upstream container hostnames once at startup and caches the IP. When `docker compose up` recreated backend containers (e.g. adding a new service triggered dependency recreation), Nginx kept routing to a stale IP that Docker's DNS had reassigned to a *different* container — request bodies looked fine, response was a clean but wrong 404 from the wrong service. Fixed by adding `resolver 127.0.0.11 valid=10s;` (Docker's embedded DNS) and using a `set $upstream ...; proxy_pass $upstream;` variable instead of a static `upstream {}` block, which forces Nginx to re-resolve on every request. See `nginx/nginx.conf`.

## Phase 2 plan (CI/CD + EC2 deploy) — not started

1. **Tests + GitHub Actions CI** — a workflow that lints and runs each service's existing pytest suite on PRs (all services already have `requirements-dev.txt` + tests; CI just needs to run them per-service, e.g. a matrix job).
2. **EC2 provisioning** (user does the actual AWS console/CLI steps, Claude guides): small Ubuntu instance, security group (80/443 open, 22 restricted), Docker + Docker Compose plugin installed, Elastic IP so the portfolio link is stable.
3. **CD pipeline**: `docker-compose.prod.yml` that pulls prebuilt images from GHCR instead of building locally + a GitHub Actions workflow that builds each service's image, pushes to `ghcr.io`, then SSHes into EC2 to `docker compose pull && docker compose up -d`.
4. **First deploy + smoke test**: verify the live EC2 URL end-to-end (same acceptance test used locally — register, login, real-time chat with ticks).
5. **README pass**: architecture diagram, screenshots, setup instructions, the "Known limitations" section above reframed as "Future Improvements" — this is what makes it read as a portfolio piece.

When resuming: re-read this file, confirm current git status, and pick up at step 1 above unless the user says otherwise.

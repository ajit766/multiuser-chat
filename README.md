# Multi-User Chat

A WhatsApp-style 1:1 chat app, built as a microservices learning project:
six Python/FastAPI backend services, a React frontend, Postgres, Redis, and
RabbitMQ, all Dockerized behind a single Nginx entry point.

See `PRD.md` for the original requirements, `WALKTHROUGH.md` for a detailed
tour of the design (with a guide to running each service manually so you
can watch logs), and `CLAUDE.md` for design decisions, known limitations,
and project history.

## Prerequisites

- Docker Desktop (or Docker Engine + the Compose plugin) installed and running.

## Quick start

```bash
cp .env.example .env   # first time only - already done in this checkout
docker compose up -d --build
```

The first build takes a minute or two. Postgres and RabbitMQ have health
checks, so dependent services will wait for them automatically. Once
`docker compose ps` shows everything `Up`, open:

**http://localhost:8000**

## Testing the app yourself

1. Open http://localhost:8000 in two browser windows (a normal window plus
   an incognito/private one works well, so they don't share login state).
2. In window A, click **Register**, create an account (e.g. username
   `alice`, password `password123`, first/last name).
3. In window B, register a second account (e.g. `bob`).
4. Back in window A, `bob` should appear in the user list — click
   **Start Chat** and send a message.
5. You should immediately see a single gray checkmark (✓) — the message is
   saved, that's the "sent" state.
6. In window B, open the chat with `alice` — the message should already be
   there.
7. If window B was open (Bob online) *at the moment* Alice sent the
   message, watch window A: the tick should flip to a double blue
   checkmark (✓✓) within about a second, with no reload needed.
8. Now try the offline case: close window B entirely, send another message
   from window A — it should stay at a single tick. Reopen window B and
   log back in as `bob` — watch window A's tick flip to double
   automatically the moment Bob reconnects, again with no reload.

That last step is the interesting one: it's exercising the
online/offline-aware delivery pipeline (RabbitMQ + a background worker +
presence tracking), not just a simple database write. `WALKTHROUGH.md`
explains exactly how it works if you want the full mechanics.

## Poking around while testing

- **RabbitMQ management UI**: http://localhost:15672 (user/pass
  `chatapp`/`chatapp`) — watch the `message.created` queue fill and drain.
- **Service logs**: `docker compose logs -f <service-name>`, e.g.
  `docker compose logs -f delivery-service` to watch delivery decisions
  happen live.
- **Run one service in the foreground** (own terminal, logs visible) with
  `docker compose up --build <service-name>` — see `WALKTHROUGH.md` for a
  step-by-step guided tour doing exactly this for every service.

## Running the automated tests

Each service has its own test suite and runs independently of the rest of
the stack:

```bash
cd services/<service-name>
docker run --rm -v "$(pwd)":/app -w /app python:3.12-slim \
  bash -c "pip install -q -r requirements-dev.txt && pytest -q"
```

The frontend's build (which also type-checks) can be run with:

```bash
cd frontend
npm install
npm run build
```

## Stopping everything

```bash
docker compose down        # stop and remove containers, keep the data (Postgres/Redis/RabbitMQ volumes)
docker compose down -v     # also wipe that data for a completely clean slate
```

## Project structure

```
services/            six FastAPI microservices - see WALKTHROUGH.md for what each one does
  user-service/       owns users_db, registration, listing users
  auth-service/        login/logout, issues JWTs
  message-service/    owns messages_db, send/fetch messages, publishes delivery events
  presence-service/    Redis-backed online/offline tracking
  gateway-service/     holds live WebSocket connections, pushes real-time updates
  delivery-service/    background worker, consumes queue events, drives delivery
frontend/            React + Vite + TypeScript UI
nginx/               reverse proxy (the public entry point) + serves the built frontend
docker-compose.yml    local dev orchestration
.env.example          copy to .env before first run
```

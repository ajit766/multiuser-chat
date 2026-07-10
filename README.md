# Multi-User Chat

A WhatsApp-style 1:1 chat app, built as a microservices learning project:
six Python/FastAPI backend services, a React frontend, Postgres, Redis, and
RabbitMQ, all Dockerized behind a single Nginx entry point, deployed to a
single EC2 instance with GitHub Actions CI/CD.

**Live demo**: http://52.201.254.226 (a bare EC2 IP, no domain/TLS yet —
see Future Improvements)

See `PRD.md` for the original requirements, `WALKTHROUGH.md` for a detailed
tour of the design (with a guide to running each service manually so you
can watch logs), and `CLAUDE.md` for design decisions and project history.

## Architecture

```
Browser ──▶ Nginx (single public entry point: :80)
              │
              ├── /auth, /users, /messages  ──▶  auth / user / message-service
              │                                          │
              │                                          ▼ message.created (RabbitMQ)
              │                                   delivery-service (worker)
              │                                    │        │
              └── /ws (WebSocket, stays open) ──▶ gateway-service ◀── presence-service (Redis)
                                                          ▲
                                                          └── push on delivery
```

- **user-service** — owns `users_db` (Postgres). Registration, listing users, bcrypt password hashing.
- **auth-service** — no DB of its own; calls user-service internally to verify credentials, issues JWTs.
- **message-service** — owns `messages_db` (Postgres). Send/fetch messages, publishes delivery events to RabbitMQ, drives the `SENT → PENDING → DELIVERED` status machine.
- **presence-service** — Redis-backed online/offline tracking with a TTL heartbeat.
- **gateway-service** — holds every live WebSocket connection; the only component that can push to a specific user in real time.
- **delivery-service** — background worker (no HTTP server), consumes queue events, decides whether to push live or mark pending.
- **nginx** — the single public door; reverse-proxies REST + WebSocket traffic and serves the built React frontend.

Full request-by-request walkthrough (with an explanation of exactly how
the WebSocket/presence/delivery mechanics work) is in `WALKTHROUGH.md`.

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

## CI/CD

- **CI** (`.github/workflows/ci.yml`, runs on every push/PR): lint (`ruff`)
  + test (`pytest`) per service, plus a separate `docker build` of each
  service's actual production image — that second check exists because a
  passing test suite doesn't guarantee the production image itself
  builds; it caught a real missing-dependency bug during development that
  the lint+test job alone couldn't see.
- **CD** (`.github/workflows/cd.yml`, runs after CI passes on `main`):
  builds and pushes each service's image to GHCR, then SSHes into the EC2
  box to `git pull` + `docker compose pull` + `up -d`. `docker-compose.prod.yml`
  is the production Compose file — same services, but pulling prebuilt
  images instead of building locally, and publishing only port 80.

Deployment target is a single `t3.micro`/`t2.micro` EC2 instance (free
tier) with a 2GB swap file added — without swap, 10 containers (6
services + Postgres + Redis + RabbitMQ + Nginx) on 1GB RAM risks the OOM
killer taking one down under load.

## Future improvements

Things deliberately left out of v1, either to keep scope PRD-sized or
because they're natural next steps once this needs to handle more than a
demo's worth of traffic:

- **Multi-instance gateway-service.** The live-connection map is in-memory
  and single-instance today. Scaling to multiple instances needs shared
  state (Redis pub/sub) so a push for a user connected to instance B
  reaches them via instance A.
- **HTTPS + a real domain.** Currently plain HTTP on a bare IP. Next step:
  a domain + Let's Encrypt (Caddy or certbot) in front of Nginx.
- **No frontend WebSocket auto-reconnect.** If the socket drops (phone
  backgrounded, laptop sleeps, network blip), the user has to reload the
  page. The server-side presence/offline detection is correct either way
  — this is purely a client-side UX gap.
- **No dead-letter queue.** A poison message on the `message.created`
  queue is logged and dropped, not retried — fine for a demo, not for
  production message volume.
- **Stateless-JWT logout only.** No server-side revocation blocklist;
  logout is client-side token deletion.
- **No group chat, read receipts beyond delivery, or message pagination
  UI** — matches the PRD's 1:1-chat scope.

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

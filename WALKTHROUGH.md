# Design & Manual Walkthrough

This doc explains how the system works end to end, and how to run each
service manually (in its own foreground terminal, logs visible) so you can
watch a message travel through the whole system instead of taking it on
faith.

## The design, end to end

The whole system exists to answer one question: **when Alice sends Bob a
message, how does it get to him instantly (if he's online) while still
being safe if he isn't?** Everything else is scaffolding around that.

The message lifecycle, in order:

1. **Alice authenticates.** `auth-service` doesn't store passwords — it
   calls `user-service`'s internal endpoint
   (`GET /internal/users/by-username/{username}`) to fetch the bcrypt hash,
   verifies it, and issues a JWT (`auth-service/app/main.py` →
   `user_client.py`). This is the fix for the original "Auth and User share
   a DB" gap — it's a real network call, not a shared table.
2. **Alice sends a message.** `POST /messages` hits
   `message-service/app/main.py:send_message`. It writes to Postgres first
   (`status=SENT`) — that's the durability guarantee — *then* publishes a
   `message.created` event to RabbitMQ (`publisher.py`). If the publish
   fails, the message is still safely stored; only the real-time push is
   delayed.
3. **`delivery-service`** (`app/worker.py`) is a plain Python process with
   no HTTP server — it just blocks on `channel.start_consuming()`. For each
   event it asks `presence-service` "is Bob online?" If yes, it calls
   `gateway-service`'s internal `/internal/push` to shove the message down
   Bob's live socket, then tells `message-service` to mark it `DELIVERED`,
   then tells `gateway-service` to push a `status_update` back to Alice
   too, so her tick flips without a reload. This is a **one-shot attempt**
   — if Bob is offline, or the push otherwise fails, it marks the message
   `PENDING` (`clients.mark_message_pending`) and moves on. It never
   revisits it itself.
4. **`gateway-service`** is the only piece that holds a live WebSocket per
   user (`connection_manager.py` — just an in-memory dict in v1).
   Everything else talks to it over plain HTTP.
5. **`nginx`** is the only public door — it reverse-proxies REST calls by
   path prefix and proxies the WS upgrade, and also serves the built React
   app as static files. `/internal/*` routes are deliberately never
   proxied here.
6. **Catching up `PENDING` messages: the WS-connect trigger.** The moment
   Bob's WebSocket connects — not when he opens a specific chat —
   `gateway-service` calls `message-service`'s
   `POST /internal/messages/mark-delivered-for-user`
   (`message_client.catch_up_pending_messages`), which bulk-marks *every*
   `SENT`/`PENDING` message addressed to Bob as `DELIVERED` in one query
   (`crud.mark_delivered_for_user`) — across every sender he has pending
   messages from, not just whichever conversation he happens to click into
   first. `gateway-service` then pushes a `status_update` to each original
   sender directly via its own connection map (no extra HTTP hop, since it
   already holds those sockets if they're connected). `message-service`'s
   `GET /messages/{other_user_id}` still does a smaller, conversation-scoped
   version of the same thing (`crud.mark_delivered_for_recipient`) as a
   fallback, in case the connect-time call failed.

This two-trigger design (attempt-at-send, catch-up-at-reconnect) exists
because a single one-shot attempt has no way to self-correct once the
recipient comes back online — something has to re-check pending messages,
and "the moment they reconnect" is the natural place to do it once, for
everything at once, rather than piecemeal as they open each chat.

Every service that needs to know "who is this request from" decodes the
JWT locally (same shared secret) rather than calling `auth-service` per
request — that's why `auth.py` is duplicated in a few services rather than
centralized.

## Nginx vs. gateway-service — who's the "API Gateway"?

**Nginx is the actual API Gateway.** It's the only thing with a door open
to the outside world — every request from your browser, whether it's a
login, a fetch-messages call, or opening the chat socket, goes to Nginx
first. Nginx's whole job is: *look at the URL, decide which internal
service should handle it, forward the request there.*

**`gateway-service` is not really a gateway in the traffic-routing sense —
it's a "live connection holder."** Its job has nothing to do with routing.
Its only job is: *keep a persistent, always-open pipe to every connected
browser, and remember which pipe belongs to which user, so other services
can reach out and push something down that pipe at any moment.*

The name is a bit of a leftover from the original hand-drawn design, which
had one box labeled "API Gateway — supports websocket," bundling both jobs
into one bubble. Implementing it split that bubble into two real pieces,
because they're fundamentally different kinds of work.

### An analogy: postcards vs. phone calls

- **A normal HTTP request** (login, register, fetch messages) **is a
  postcard.** You write it, send it, someone reads it and mails a reply,
  and that's it — the "conversation" is over. Every request is a brand new
  postcard. Nobody's standing by waiting.
- **A WebSocket is a phone call.** Once connected, the line stays open.
  Either side can speak at any moment without redialing. This is the only
  way the *server* can "speak first" — the instant Alice sends Bob a
  message, the server needs to interrupt Bob's already-open line and say
  "here's something new," rather than Bob having to keep calling back
  every few seconds asking "anything new?" (that repeated-asking approach
  is called polling, and WebSockets exist to avoid it).

Nginx handles postcards. `gateway-service` handles the one phone call that
stays open for as long as your browser tab is alive.

### Walking through what actually happens

1. **You load the page.** Your browser asks for `http://localhost:8000/`.
   This hits Nginx, which hands back the built React app — Nginx acting as
   a plain web server (`location /` in `nginx.conf`, `try_files $uri
   /index.html`).
2. **You log in.** The React app POSTs to `/auth/login`. Nginx sees the
   `/auth/` prefix and forwards it to `auth-service`. Postcard — request,
   response, done.
3. **The app opens the chat socket.** Right after login, your browser runs
   `new WebSocket("ws://localhost:8000/ws?token=<your JWT>")`
   (`frontend/src/ws/useChatSocket.ts`). This looks like an HTTP request
   but carries a special header asking to *upgrade* the connection into a
   WebSocket. It still goes through Nginx first, because Nginx is the only
   thing your browser can reach. Nginx sees `/ws` and forwards it to
   `gateway-service`, configured to preserve the "upgrade" instead of
   treating it like a normal one-shot call
   (`proxy_set_header Upgrade $http_upgrade;`).
4. **`gateway-service` accepts the call.** It checks your JWT, and if
   valid, "accepts" the upgrade. At that exact moment the HTTP request
   stops being a request — it becomes a raw, persistent, two-way pipe
   between your browser and `gateway-service` that just stays open.
   `gateway-service` writes your entry into a simple in-memory address
   book: `{"your-user-id": <this exact open pipe>}`
   (`connection_manager.py`).
5. **Someone sends you a message.** Separate, ordinary postcard: the
   sender's browser POSTs to `/messages` through Nginx to
   `message-service`, which saves it and drops an event on a queue.
   `delivery-service` picks that up, checks "is the recipient online?",
   and if so, sends its own ordinary HTTP postcard to `gateway-service`'s
   internal endpoint: *"if you're holding a pipe open to user Bob, write
   this onto it."*
6. **`gateway-service` looks Bob up in its address book, finds his open
   pipe, and writes the message directly onto it.** Bob's browser, which
   has been quietly holding the other end of that pipe the whole time
   (`ws.onmessage = ...`), receives it instantly. No polling, no delay, no
   new connection.

### Why bother with two pieces instead of exposing gateway-service directly?

Because we want exactly **one** public front door to the whole system —
for security (nothing else is ever reachable from the internet), for
simplicity (one place to manage TLS certificates later, one place to add
rate limiting), and because internal services can change freely without
the browser needing to know or care. Like a company with a single
reception desk: visitors never wander the halls looking for the right
office — they check in at reception, and reception (Nginx) buzzes them
through to the right department. `gateway-service` is one of those
departments — the one whose entire job is "keep a phone line open."

```
                         ┌──────────────────────────────┐
                         │            Nginx              │
Browser  ───────────────▶│  (the ONE public front door)  │
  │                      │  routes by URL path            │
  │                      └───────────┬────────────────────┘
  │  normal requests            │  /ws  (upgraded, stays open)
  │  (login, register,          │
  │   send message,             ▼
  │   fetch history)   ┌──────────────────┐
  └────────────────────▶│  auth / user /   │        ┌───────────────────┐
                        │  message-service │        │  gateway-service   │
                        └──────────────────┘        │  (holds every live │
                                  │                  │   WebSocket pipe)  │
                                  │ message.created   └─────────┬─────────┘
                                  ▼ (via RabbitMQ)               ▲
                        ┌──────────────────┐   "push to Bob"    │
                        │ delivery-service  │────────────────────┘
                        └──────────────────┘   (internal HTTP call)
```

Everything left of Nginx is postcards. The `/ws` line on the right is the
one phone call that never hangs up.

## How presence tracking works (online/offline detection)

`presence-service` is completely passive — it doesn't watch or poll
anything on its own. It just reacts when someone calls it:
`POST /presence/online`, `POST /presence/offline`, or
`GET /presence/{id}`. There are exactly three moments something calls
into it:

1. **Your WebSocket connects** (you open the app / log in) →
   `gateway-service` calls `POST /presence/online`, right after
   `manager.connect(...)` in `gateway-service/app/main.py`.
2. **Your WebSocket disconnects cleanly** (you close the tab, log out,
   navigate away) → `gateway-service`'s `finally` block calls
   `POST /presence/offline`.
3. **Every 30 seconds while connected**, your browser sends a tiny
   `{"type": "ping"}` over the still-open socket
   (`frontend/src/ws/useChatSocket.ts`), and `gateway-service` responds by
   calling `POST /presence/online` again — a heartbeat.

### The trick: Redis TTL, not a "watcher"

When `gateway-service` marks you online, it sets a Redis key with an
expiration:

```
SET presence:<your-id> "online" EX 90
```

`EX 90` means: Redis will automatically delete this key in 90 seconds, no
matter what, unless something re-sets it first. Nobody has to check on
you — Redis's own internal clock handles the deletion. Think of it like an
office door badge that auto-deactivates 90 minutes after your last swipe,
whether or not you told the front desk you left.

Your 30-second ping is what keeps re-swiping the badge. Since 30s pings
are well inside the 90s expiry window, one slow or dropped ping doesn't
cause a false "offline" — there's still 60+ seconds of buffer left.

`GET /presence/{id}` just checks: does this key still exist in Redis? If
yes → online. If it expired or was deleted → offline.

### What actually happens when you close the tab

Two different paths, resolving at very different speeds:

**Path A — you close the tab normally** (click the X, navigate away, log
out). Your browser sends a proper "closing this connection" message down
the socket before it tears down. `gateway-service` is sitting in a loop
waiting for the next message (`await websocket.receive_json()`); the
moment that close message arrives, it raises a `WebSocketDisconnect`,
jumping straight to the `finally` block: it removes you from its
in-memory connection map and immediately calls `POST /presence/offline`,
deleting your Redis key right then. **Presence finds out almost
instantly.**

**Path B — your connection dies silently** (laptop sleeps mid-session,
WiFi cuts out, browser crashes, phone loses signal). No close message is
ever sent — the connection just goes silent. The server has no fast way
to know this happened. But since you're gone, your ping heartbeat also
stops — nothing is re-swiping the badge anymore. Within **at most 90
seconds**, the Redis key expires on its own, and `GET /presence/{id}`
starts correctly returning "offline" again. **This is the entire reason
the TTL exists** — a safety net for the case where the "please mark me
offline" message can never arrive because the thing that would send it is
already gone.

(Subtlety: in Path B, `gateway-service`'s own in-memory socket map might
still hold onto that dead connection for a while — it isn't cleaned up
until you reconnect, at which point `connection_manager.connect()`
replaces the old dead entry with your new live one. Harmless, since
everything that *checks* presence goes through Redis, not that in-memory
map.)

### Coming back online

Nothing special — it's the exact same "moment 1" as the very first time.
You reopen the app, a brand new WebSocket connects through Nginx to
`gateway-service`, and it calls `POST /presence/online` again — which is
also the exact moment that triggers the pending-message catch-up
described above.

### A timeline

```
t=0s     tab opens, WS connects  → presence key SET, expires at t=90s
t=30s    ping                    → key refreshed, now expires at t=120s
t=60s    ping                    → key refreshed, now expires at t=150s
t=75s    laptop sleeps, WS dies silently, no more pings
t=150s   nothing refreshed the key → Redis auto-deletes it → now "offline"

vs.

t=0s     tab opens, WS connects  → presence key SET
t=45s    user closes tab cleanly → close frame received →
         gateway-service immediately calls mark_offline → "offline" right away
```

## Running it manually with logs visible

Native Python won't work directly for this on a machine running Python
3.14 — the pinned `pydantic-core` wheels don't have 3.14 builds. Instead,
run each service in its own foreground terminal via Docker Compose, which
streams logs live and is exactly how it runs in production anyway.

**Setup — one terminal, start infra only:**

```bash
cd ~/playground/multiuser-chat
docker compose down
docker compose up -d postgres redis rabbitmq
```

Then open a new terminal tab per service below, run the
`docker compose up --build <service>` command in it, and leave it running
so you can watch logs while you `curl` from yet another tab.

### 1. user-service (terminal A: `docker compose up --build user-service`)

```bash
curl -X POST http://localhost:8001/users -H 'Content-Type: application/json' \
  -d '{"username":"trace","password":"password123","first_name":"T","last_name":"1"}'
```

Watch terminal A log the `POST /users 201`. Try it again with the same
username — watch it `409`. Open `services/user-service/app/main.py` next
to it and trace `register_user` → `crud.create_user` →
`security.hash_password`.

### 2. auth-service (terminal B: `docker compose up --build auth-service`)

```bash
curl -X POST http://localhost:8002/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"trace","password":"password123"}'
```

This is the interesting one — watch **both** terminals A and B. B logs the
incoming login; A logs an incoming
`GET /internal/users/by-username/trace` — that's the live
service-to-service call, not a shared database. Save the `access_token`
from the response.

### 3. message-service (terminal C) + RabbitMQ UI

```bash
TOKEN="<paste access_token>"
BOB_ID="<some other user's id from GET /users>"
curl -X POST http://localhost:8003/messages -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"to_user_id\":\"$BOB_ID\",\"message\":\"hi\"}"
```

Open **http://localhost:15672** (user/pass `chatapp`/`chatapp`) → Queues →
`message.created` — you'll see the message count tick up if
`delivery-service` isn't running yet, which is a nice concrete look at "a
durable queue with no consumer."

### 4. presence-service (terminal D)

```bash
curl -X POST http://localhost:8004/presence/online -H "X-Internal-Api-Key: change-me-internal-dev-key" \
  -H 'Content-Type: application/json' -d "{\"user_id\":\"$BOB_ID\"}"
curl http://localhost:8004/presence/$BOB_ID -H "X-Internal-Api-Key: change-me-internal-dev-key"
```

### 5. gateway-service (terminal E) — the real-time piece

Open your browser's devtools console on any page and run:

```js
const ws = new WebSocket(`ws://localhost:8005/ws?token=${TOKEN}`);  // use Bob's token here
ws.onmessage = e => console.log("received:", e.data);
```

Watch terminal E log the `101` upgrade, and terminal D (presence) log the
online call.

### 6. delivery-service (terminal F)

Now send another message (step 3) to Bob's user id while his socket from
step 5 is open. Watch terminal F log `Delivered message ... to ...`, watch
your browser console print the pushed JSON, and watch terminal C
(message-service) log the internal `PATCH .../delivered` call.

Once you've got a feel for each piece in isolation, `Ctrl+C` out and go
back to `docker compose up -d --build` for normal use.

## Quick reference

| Service | Port | Notes |
| --- | --- | --- |
| nginx (public entry point) | 8000 | serves frontend + proxies everything else |
| user-service | 8001 | |
| auth-service | 8002 | |
| message-service | 8003 | |
| presence-service | 8004 | internal-only, needs `X-Internal-Api-Key` header |
| gateway-service | 8005 | WS at `/ws?token=<jwt>`, internal push at `/internal/push` |
| RabbitMQ management UI | 15672 | user/pass `chatapp`/`chatapp` (from `.env`) |
| Postgres | 5432 | two databases: `users_db`, `messages_db` |
| Redis | 6379 | presence TTL keys |

`INTERNAL_API_KEY` default in this checkout: `change-me-internal-dev-key`
(from `.env`, generated from `.env.example`).

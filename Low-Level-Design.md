# Multi-User Chat Application - Low-Level Design

## 1. Scope

This document expands the high-level design into implementation details for the first production-shaped version of the chat system.

The design covers:

- AWS-based deployment.
- Python/FastAPI backend microservices.
- Cognito-backed authentication through an Auth Service facade.
- API Gateway HTTP and WebSocket routing.
- User profile storage in Aurora PostgreSQL.
- Message storage in DynamoDB.
- Async delivery using MSK Serverless.
- Presence and recent-message caching in ElastiCache.
- One-to-one chat only.
- One conversation between any two users.
- Single tick for `SENT`.
- Double tick for `DELIVERED`.

Out of scope for this version:

- Group chat.
- Read receipts beyond delivery acknowledgement.
- Typing indicators.
- Attachments.
- Push notifications.
- Chatbot support.
- Kubernetes/EKS.

## 2. Deployment Units

Each backend service is implemented as a Python FastAPI application and packaged as a Docker image.

| Service | Runtime | Deployment | Public? |
| --- | --- | --- | --- |
| Auth Service | FastAPI | ECS Fargate | Through API Gateway only |
| User Service | FastAPI | ECS Fargate | Through API Gateway only |
| Socket Manager | FastAPI | ECS Fargate | Through API Gateway WebSocket only |
| Message Service | FastAPI | ECS Fargate | Through API Gateway and internal calls |
| Delivery Service | Python worker/FastAPI admin endpoints | ECS Fargate | Internal only |
| Message Outbox Worker | Python worker | ECS Fargate | Internal only |

The Socket Manager can start with one ECS task as agreed. Other services can run with at least two tasks for availability. The Socket Manager can be horizontally scaled later because API Gateway owns the external WebSocket connections and Presence Cache stores connection state.

## 3. AWS Components

| Component | AWS Service |
| --- | --- |
| HTTP API | Amazon API Gateway HTTP API |
| WebSocket API | Amazon API Gateway WebSocket API |
| Compute | Amazon ECS on AWS Fargate |
| Container images | Amazon ECR |
| Auth provider | Amazon Cognito User Pools |
| User DB | Amazon Aurora PostgreSQL |
| Message DB | Amazon DynamoDB |
| Broker | Amazon MSK Serverless |
| Cache | Amazon ElastiCache for Redis OSS or Valkey |
| Secrets | AWS Secrets Manager |
| Config | AWS Systems Manager Parameter Store |
| Permissions | AWS IAM |
| Logs/metrics/traces | CloudWatch + X-Ray/OpenTelemetry |
| Infrastructure | AWS CDK in Python |

## 4. Network Layout

Recommended AWS network shape:

```text
Internet
  -> CloudFront / API Gateway
  -> API Gateway HTTP API / WebSocket API
  -> VPC Link
  -> Internal Load Balancer
  -> ECS Fargate services in private subnets
```

Private resources:

- ECS tasks.
- Aurora PostgreSQL.
- ElastiCache.
- MSK Serverless.
- Internal load balancers.

Public resources:

- CloudFront.
- S3 frontend bucket.
- API Gateway.

Security group rules:

- API Gateway VPC Link can reach internal load balancer.
- Internal load balancer can reach ECS service ports.
- User Service can reach Aurora.
- Message Service can reach DynamoDB, ElastiCache, and MSK.
- Socket Manager can reach ElastiCache, Message Service, Delivery Service, and API Gateway Management API.
- Delivery Service can reach MSK, ElastiCache, Message Service, and Socket Manager.

## 5. API Gateway Routing

### 5.1 HTTP Routes

| Method | Route | Target |
| --- | --- | --- |
| `POST` | `/auth/register` | Auth Service |
| `POST` | `/auth/login` | Auth Service |
| `POST` | `/auth/logout` | Auth Service |
| `GET` | `/users` | User Service |
| `GET` | `/users/{user_id}` | User Service |
| `GET` | `/messages/{other_user_id}` | Message Service |

Protected HTTP routes require JWT validation. Registration and login are public.

### 5.2 WebSocket Routes

API Gateway WebSocket route selection:

```text
$request.body.action
```

| WebSocket Route | Target | Purpose |
| --- | --- | --- |
| `$connect` | Socket Manager | Validate token and register connection |
| `$disconnect` | Socket Manager | Remove presence and connection mapping |
| `send_message` | Socket Manager | Send chat message |
| `ack_delivered` | Socket Manager | Client confirms message receipt |
| `ping` | Socket Manager | Refresh connection heartbeat |
| `$default` | Socket Manager | Return unsupported action error |

For browser clients, the JWT can be passed during WebSocket connection using a short-lived token in query string or subprotocol. Query-string token usage requires API Gateway access logs to avoid logging sensitive query parameters.

## 6. Authentication and Authorization

### 6.1 Auth Service and Cognito

Auth Service is the application-facing facade. Cognito stores credentials and issues JWT tokens.

Auth Service responsibilities:

- Validate registration input.
- Create Cognito users.
- Set permanent password in Cognito.
- Call User Service to create profile.
- Roll back Cognito user if profile creation fails.
- Authenticate login using Cognito.
- Return JWT tokens to client.
- Logout through Cognito token revocation/global sign-out.

Auth Service does not store passwords or password hashes.

### 6.2 JWT Usage

Client stores JWT after login and sends it as:

```http
Authorization: Bearer <access_token>
```

For HTTP requests:

- API Gateway validates JWT when possible.
- Backend services also validate trusted identity context or verify JWT for defense in depth.

For WebSocket:

- Socket Manager validates token during `$connect`.
- Socket Manager extracts `cognito_user_id`.
- Socket Manager resolves application `user_id` from User Service or a local cache.

Required claims:

```text
sub                 Cognito user id
username            login username
exp                 expiry
iat                 issued at
iss                 Cognito issuer
client_id/aud       expected app client
```

### 6.3 Internal Service Authorization

Service-to-service calls stay inside the VPC. AWS IAM roles control AWS resource access.

Recommended IAM split:

| Role | Permissions |
| --- | --- |
| Auth Service task role | Cognito admin APIs, call User Service, read config/secrets |
| User Service task role | Read Aurora secret, write/read Aurora |
| Socket Manager task role | ElastiCache access, API Gateway manage connections, call Message/Delivery services |
| Message Service task role | DynamoDB read/write, MSK publish, ElastiCache access |
| Delivery Service task role | MSK consume, ElastiCache read, call Socket Manager and Message Service |
| Outbox Worker task role | DynamoDB outbox read/write, MSK publish |

## 7. Public API Contracts

### 7.1 Register

```http
POST /auth/register
Content-Type: application/json
```

Request:

```json
{
  "username": "ajit",
  "password": "password",
  "first_name": "Ajit",
  "last_name": "G"
}
```

Response `201`:

```json
{
  "user_id": "018f7b64-79c2-7b0a-9f77-6a9f16c40711",
  "username": "ajit"
}
```

Errors:

| Status | Reason |
| --- | --- |
| `400` | Missing username, password, or first name |
| `409` | Username already exists |
| `500` | Registration failed after rollback |

### 7.2 Login

```http
POST /auth/login
Content-Type: application/json
```

Request:

```json
{
  "username": "ajit",
  "password": "password"
}
```

Response `200`:

```json
{
  "access_token": "jwt",
  "id_token": "jwt",
  "refresh_token": "token",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

Errors:

| Status | Reason |
| --- | --- |
| `400` | Missing username or password |
| `401` | Invalid username/password |

### 7.3 Logout

```http
POST /auth/logout
Authorization: Bearer <access_token>
```

Response `204`.

### 7.4 List/Search Users

```http
GET /users?query=aj&limit=25&cursor=<cursor>
Authorization: Bearer <access_token>
```

Response `200`:

```json
{
  "items": [
    {
      "user_id": "018f7b64-79c2-7b0a-9f77-6a9f16c40711",
      "username": "ajit",
      "first_name": "Ajit",
      "last_name": "G"
    }
  ],
  "next_cursor": "opaque-cursor"
}
```

Rules:

- Exclude the logged-in user.
- Default `limit` is `25`.
- Maximum `limit` is `100`.
- Cursor is opaque and generated by User Service.

### 7.5 Fetch Messages With Another User

```http
GET /messages/{other_user_id}?limit=50&cursor=<cursor>
Authorization: Bearer <access_token>
```

Response `200`:

```json
{
  "conversation_id": "dm_4c59...",
  "items": [
    {
      "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c",
      "from_user_id": "user-a",
      "to_user_id": "user-b",
      "body": "hello",
      "status": "SENT",
      "created_at": "2026-07-07T10:30:00Z",
      "delivered_at": null
    }
  ],
  "next_cursor": "opaque-cursor"
}
```

Rules:

- Message Service computes `conversation_id` from current user and `other_user_id`.
- Default `limit` is `50`.
- Maximum `limit` is `100`.
- Return newest page first unless UI requires oldest-first rendering.

## 8. WebSocket Contracts

### 8.1 Connect

Client connects:

```text
wss://<api-id>.execute-api.<region>.amazonaws.com/<stage>?access_token=<jwt>
```

Socket Manager receives:

```json
{
  "requestContext": {
    "connectionId": "abc123",
    "routeKey": "$connect"
  }
}
```

Socket Manager actions:

1. Validate JWT.
2. Resolve `user_id`.
3. Store presence.
4. Fetch undelivered messages.
5. Push undelivered messages to the connection.

### 8.2 Send Message

Client event:

```json
{
  "action": "send_message",
  "client_message_id": "client-generated-uuid",
  "to_user_id": "user-b",
  "body": "hello"
}
```

Server single-tick response:

```json
{
  "type": "message_sent",
  "client_message_id": "client-generated-uuid",
  "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c",
  "conversation_id": "dm_4c59...",
  "status": "SENT",
  "created_at": "2026-07-07T10:30:00Z"
}
```

Validation:

- `client_message_id` is required.
- `to_user_id` is required.
- `body` is required.
- `body` max length: 4,000 characters for phase 1.
- Sender cannot send message to self.

### 8.3 Receive Message

Recipient receives:

```json
{
  "type": "message_received",
  "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c",
  "conversation_id": "dm_4c59...",
  "from_user_id": "user-a",
  "to_user_id": "user-b",
  "body": "hello",
  "status": "SENT",
  "created_at": "2026-07-07T10:30:00Z"
}
```

### 8.4 Delivery Acknowledgement

Recipient sends:

```json
{
  "action": "ack_delivered",
  "message_ids": [
    "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  ]
}
```

Sender receives double-tick event:

```json
{
  "type": "message_delivered",
  "message_ids": [
    "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  ],
  "delivered_at": "2026-07-07T10:30:02Z"
}
```

### 8.5 Error Event

```json
{
  "type": "error",
  "request_id": "req-123",
  "code": "VALIDATION_ERROR",
  "message": "body is required"
}
```

## 9. Internal Service APIs

### 9.1 Auth Service -> User Service

```http
POST /internal/users
```

Request:

```json
{
  "cognito_user_id": "cognito-sub",
  "username": "ajit",
  "first_name": "Ajit",
  "last_name": "G"
}
```

Response:

```json
{
  "user_id": "018f7b64-79c2-7b0a-9f77-6a9f16c40711"
}
```

### 9.2 Socket Manager -> Message Service

```http
POST /internal/messages
```

Request:

```json
{
  "from_user_id": "user-a",
  "to_user_id": "user-b",
  "client_message_id": "client-generated-uuid",
  "body": "hello"
}
```

Response:

```json
{
  "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c",
  "conversation_id": "dm_4c59...",
  "status": "SENT",
  "created_at": "2026-07-07T10:30:00Z"
}
```

### 9.3 Socket Manager -> Message Service: Undelivered

```http
GET /internal/messages/undelivered?user_id=user-b&limit=100&cursor=<cursor>
```

Response:

```json
{
  "items": [],
  "next_cursor": null
}
```

### 9.4 Delivery Service -> Message Service: Mark Delivered

```http
POST /internal/messages/delivered
```

Request:

```json
{
  "delivered_to_user_id": "user-b",
  "message_ids": [
    "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  ],
  "delivered_at": "2026-07-07T10:30:02Z"
}
```

Response:

```json
{
  "updated_message_ids": [
    "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  ]
}
```

### 9.5 Delivery Service -> Socket Manager: Push Event

```http
POST /internal/socket/push
```

Request:

```json
{
  "user_id": "user-b",
  "event": {
    "type": "message_received",
    "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  }
}
```

Response:

```json
{
  "delivered_to_gateway": true
}
```

This means the event was accepted by API Gateway Management API, not that the browser client acknowledged it. The message becomes `DELIVERED` only after the client sends `ack_delivered`.

## 10. Aurora PostgreSQL Schema

### 10.1 Users Table

```sql
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    user_id UUID PRIMARY KEY,
    cognito_user_id VARCHAR(128) NOT NULL UNIQUE,
    username CITEXT NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_username ON users (username);
CREATE INDEX idx_users_first_last_name ON users (first_name, last_name);
CREATE INDEX idx_users_status ON users (status);
```

Notes:

- `CITEXT` avoids case-sensitive duplicate usernames.
- User Service should normalize and trim username before writing.
- User list/search should exclude `status != 'ACTIVE'`.

## 11. DynamoDB Design

### 11.1 `chat_messages` Table

Primary key:

```text
PK: conversation_id
SK: created_at_ms#message_id
```

Attributes:

```text
message_id
conversation_id
from_user_id
to_user_id
client_message_id
body
status
created_at_ms
created_at_iso
delivered_at_ms
delivered_at_iso
recipient_status
version
```

`recipient_status` value:

```text
{to_user_id}#{status}
```

GSI for undelivered messages:

```text
GSI name: gsi_recipient_status
GSI PK: recipient_status
GSI SK: created_at_ms#message_id
```

Example:

```text
recipient_status = user-b#SENT
```

This supports:

```text
get undelivered messages for user-b
```

Status update from `SENT` to `DELIVERED` updates `recipient_status`, which removes the item from the `user-b#SENT` query result.

### 11.2 `message_idempotency` Table

Purpose: avoid duplicate messages when the client retries.

Primary key:

```text
PK: from_user_id#client_message_id
```

Attributes:

```text
message_id
conversation_id
created_at_ms
expires_at_epoch_seconds
```

TTL:

```text
expires_at_epoch_seconds
```

Recommended TTL: 24 hours.

### 11.3 `message_outbox` Table

Purpose: guarantee that persisted messages eventually produce broker events.

Primary key:

```text
PK: event_id
```

Attributes:

```text
event_id
event_type
aggregate_id
payload
status
created_at_ms
published_at_ms
retry_count
next_retry_at_ms
```

Status values:

```text
PENDING
PUBLISHED
FAILED_RETRYABLE
FAILED_PERMANENT
```

Message Service writes `chat_messages`, `message_idempotency`, and `message_outbox` in a DynamoDB transaction. Message Outbox Worker publishes `PENDING` events to MSK and marks them `PUBLISHED`.

### 11.4 Optional `user_chats` Table

Purpose: efficient chat list ordered by latest message.

Primary key:

```text
PK: user_id
SK: updated_at_ms#conversation_id
```

Attributes:

```text
conversation_id
peer_user_id
last_message_id
last_message_preview
last_message_sender_id
updated_at_ms
```

On every message, Message Service writes two records:

- One for sender.
- One for recipient.

This table is optional for the PRD, but useful for a WhatsApp-like chat list.

## 12. Cache Design

### 12.1 Presence Cache

Key:

```text
presence:{user_id}
```

Value:

```json
{
  "user_id": "user-b",
  "connection_id": "api-gateway-connection-id",
  "connected_at": "2026-07-07T10:00:00Z",
  "last_seen_at": "2026-07-07T10:01:00Z",
  "status": "ONLINE"
}
```

TTL:

```text
90 seconds
```

Reverse lookup key:

```text
connection:{connection_id} -> user_id
```

Rules:

- Socket Manager writes presence.
- Delivery Service reads presence.
- `ping` refreshes TTL.
- `$disconnect` deletes presence only if the stored connection id matches the disconnecting connection id.

### 12.2 Message Cache

Key:

```text
recent_messages:{conversation_id}
```

Value:

```text
Redis list of recent message JSON objects
```

Rules:

- Store last 50 messages.
- TTL: 24 hours after last update.
- Message Service owns all reads/writes.
- On new message, append after DynamoDB write succeeds.
- On status update, either update cached message status or invalidate the key. Initial implementation should invalidate the key for correctness.

### 12.3 Idempotency Cache

Optional optimization:

```text
send_result:{from_user_id}:{client_message_id}
```

TTL:

```text
15 minutes
```

This can reduce DynamoDB reads on rapid client retries. DynamoDB `message_idempotency` remains the source of truth.

## 13. Message Broker Design

### 13.1 Topics

| Topic | Producer | Consumer | Partition Key |
| --- | --- | --- | --- |
| `chat.message.created.v1` | Message Outbox Worker | Delivery Service | `conversation_id` |
| `chat.message.delivered.v1` | Message Service | Delivery Service or analytics later | `from_user_id` |

Partitioning `MessageCreated` by `conversation_id` preserves ordering within one conversation.

### 13.2 `MessageCreated` Event

```json
{
  "event_id": "evt-018f7b67",
  "event_type": "MessageCreated",
  "version": 1,
  "occurred_at": "2026-07-07T10:30:00Z",
  "message": {
    "message_id": "018f7b67-9c78-7d89-a2ef-2f89738a725c",
    "client_message_id": "client-generated-uuid",
    "conversation_id": "dm_4c59...",
    "from_user_id": "user-a",
    "to_user_id": "user-b",
    "body": "hello",
    "status": "SENT",
    "created_at": "2026-07-07T10:30:00Z"
  }
}
```

### 13.3 `MessageDelivered` Event

```json
{
  "event_id": "evt-018f7b68",
  "event_type": "MessageDelivered",
  "version": 1,
  "occurred_at": "2026-07-07T10:30:02Z",
  "message_ids": [
    "018f7b67-9c78-7d89-a2ef-2f89738a725c"
  ],
  "conversation_id": "dm_4c59...",
  "from_user_id": "user-a",
  "to_user_id": "user-b",
  "delivered_at": "2026-07-07T10:30:02Z"
}
```

### 13.4 Consumer Rules

Delivery Service must be idempotent:

- Duplicate `MessageCreated` events should not create duplicate client messages.
- Duplicate delivery acknowledgements should not break status updates.
- Updating `DELIVERED` for an already delivered message should be a no-op.

## 14. Conversation ID

Message Service computes conversation id.

Algorithm:

```text
low_user_id = min(from_user_id, to_user_id)
high_user_id = max(from_user_id, to_user_id)
conversation_id = "dm_" + sha256(low_user_id + ":" + high_user_id).hex()[0:32]
```

Rules:

- Sender cannot send to self.
- Both users must exist and be active.
- No separate Conversation Service is required.

## 15. Message State Machine

```text
SENT -> DELIVERED
```

State meanings:

| Status | Meaning | UI |
| --- | --- | --- |
| `SENT` | Message is durably saved in DynamoDB | Single tick |
| `DELIVERED` | Recipient client acknowledged receipt | Double tick |

Invalid transitions:

- `DELIVERED -> SENT`
- `SENT -> SENT` except idempotent retry/no-op
- `DELIVERED -> DELIVERED` except idempotent retry/no-op

## 16. Detailed Flows

### 16.1 Registration

```text
Client -> API Gateway -> Auth Service
Auth Service validates request
Auth Service creates Cognito user
Auth Service sets permanent password in Cognito
Auth Service calls User Service
User Service inserts row in Aurora
Auth Service returns success
```

Failure handling:

- If Cognito creation fails, return error.
- If User Service fails after Cognito creation, Auth Service disables/deletes Cognito user and returns error.
- If rollback fails, emit operational alert and mark user for cleanup.

### 16.2 Login

```text
Client -> API Gateway -> Auth Service
Auth Service calls Cognito authenticate API
Cognito returns JWT tokens
Auth Service returns tokens
Client stores tokens
```

### 16.3 WebSocket Connect

```text
Client connects to API Gateway WebSocket API
API Gateway invokes Socket Manager $connect handler
Socket Manager validates JWT
Socket Manager resolves user_id
Socket Manager writes presence:{user_id}
Socket Manager writes connection:{connection_id}
Socket Manager fetches undelivered messages from Message Service
Socket Manager pushes undelivered messages through API Gateway Management API
```

### 16.4 Send Message

```text
Client sends send_message event
API Gateway forwards event to Socket Manager
Socket Manager validates connection and payload
Socket Manager calls Message Service
Message Service validates sender and recipient
Message Service computes conversation_id
Message Service checks idempotency
Message Service writes message as SENT to DynamoDB
Message Service writes outbox event in same transaction
Message Service updates recent message cache
Message Service returns SENT to Socket Manager
Socket Manager pushes single tick to sender
Outbox Worker publishes MessageCreated to MSK
Delivery Service consumes MessageCreated
Delivery Service reads Presence Cache
If recipient online, Delivery Service asks Socket Manager to push message
Socket Manager pushes message through API Gateway Management API
Recipient client sends ack_delivered
Socket Manager forwards ack to Delivery Service
Delivery Service calls Message Service to mark DELIVERED
Message Service updates DynamoDB and invalidates message cache
Delivery Service asks Socket Manager to push double tick to sender
```

### 16.5 Offline Recipient

```text
Message remains SENT
Delivery Service does not mark delivered
On reconnect, Socket Manager fetches undelivered messages
Recipient receives messages
Recipient acknowledges
Message becomes DELIVERED
```

## 17. Error Handling

### 17.1 HTTP Error Format

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "first_name is required",
    "request_id": "req-123"
  }
}
```

Common error codes:

| Code | HTTP Status |
| --- | --- |
| `VALIDATION_ERROR` | `400` |
| `UNAUTHORIZED` | `401` |
| `FORBIDDEN` | `403` |
| `NOT_FOUND` | `404` |
| `USERNAME_EXISTS` | `409` |
| `DUPLICATE_MESSAGE` | `200` with existing message result |
| `RATE_LIMITED` | `429` |
| `INTERNAL_ERROR` | `500` |

### 17.2 WebSocket Error Format

```json
{
  "type": "error",
  "code": "VALIDATION_ERROR",
  "message": "to_user_id is required",
  "request_id": "req-123"
}
```

## 18. Idempotency and Retries

### 18.1 Client Message Idempotency

Client must generate `client_message_id` for every send attempt.

Message Service uses:

```text
from_user_id + client_message_id
```

as the idempotency key.

If the same message is retried:

- Return existing `message_id`.
- Do not create a second message.
- Do not publish a second `MessageCreated` event unless the original outbox event was never published.

### 18.2 Broker Retry

Delivery Service commits consumer offset only after processing the event enough to know delivery was attempted or recipient is offline.

If Socket Manager push fails due to stale connection:

- Delete stale presence.
- Leave message `SENT`.
- Do not mark delivered.

If Delivery Service crashes:

- MSK redelivers from last committed offset.
- Idempotency prevents duplicate delivery state changes.

## 19. Rate Limits

Initial limits:

| Action | Limit |
| --- | --- |
| Register | 5 attempts per IP per hour |
| Login | 10 attempts per username per 15 minutes |
| Send message | 30 messages per user per minute |
| User search | 60 requests per user per minute |
| Fetch messages | 120 requests per user per minute |
| WebSocket ping | 1 per 30 seconds |

Enforcement:

- API Gateway for coarse limits.
- Service-level Redis counters for user-specific limits.

## 20. Observability

### 20.1 Logs

All services log structured JSON.

Required fields:

```text
timestamp
level
service
request_id
user_id
message_id
conversation_id
event_type
error_code
latency_ms
```

Do not log:

- Passwords.
- JWTs.
- Refresh tokens.
- Full WebSocket access token query strings.

### 20.2 Metrics

Key metrics:

| Metric | Source |
| --- | --- |
| `auth.register.success` | Auth Service |
| `auth.login.failure` | Auth Service |
| `users.search.latency_ms` | User Service |
| `socket.connections.active` | Socket Manager |
| `messages.sent.count` | Message Service |
| `messages.delivered.count` | Message Service |
| `messages.delivery.latency_ms` | Delivery Service |
| `messages.undelivered.count` | Message Service |
| `broker.consumer.lag` | Delivery Service |
| `cache.presence.hit_rate` | Delivery Service |
| `cache.message.hit_rate` | Message Service |
| `dynamodb.throttles` | AWS/DynamoDB |
| `aurora.connections` | AWS/RDS |

### 20.3 Alerts

Create alerts for:

- High login failure rate.
- High registration failure rate.
- MSK consumer lag.
- DynamoDB throttling.
- Aurora CPU/connection exhaustion.
- ElastiCache memory pressure.
- Delivery latency above threshold.
- Error rate above threshold per service.

## 21. Local Development

Recommended local stack:

```text
Docker Compose
PostgreSQL
Redis
Redpanda or Kafka-compatible broker
DynamoDB Local
LocalStack for selected AWS integrations if needed
```

Local services:

```text
auth-service
user-service
socket-manager
message-service
delivery-service
outbox-worker
frontend
```

Local auth options:

- Use a real Cognito dev user pool, or
- Use a mock JWT issuer for local development.

The production path should use Cognito.

## 22. Suggested Repository Structure

```text
multiuser-chat/
  frontend/
  services/
    auth-service/
      app/
      tests/
      Dockerfile
    user-service/
      app/
      tests/
      Dockerfile
    socket-manager/
      app/
      tests/
      Dockerfile
    message-service/
      app/
      tests/
      Dockerfile
    delivery-service/
      app/
      tests/
      Dockerfile
    outbox-worker/
      app/
      tests/
      Dockerfile
  libs/
    python/
      common/
        auth/
        logging/
        models/
        tracing/
  infra/
    cdk/
  docs/
```

## 23. Testing Strategy

### 23.1 Unit Tests

Cover:

- Input validation.
- Conversation id generation.
- Idempotency behavior.
- Message status transition rules.
- Cache key generation.
- Event schema serialization.

### 23.2 Integration Tests

Cover:

- Auth Service with Cognito test/mocked client.
- User Service with PostgreSQL.
- Message Service with DynamoDB Local.
- Delivery Service with local broker.
- Socket Manager with Redis.

### 23.3 End-to-End Tests

Cover:

- Register user A and B.
- Login both users.
- User A finds user B.
- User A sends message to online user B.
- User A gets single tick.
- User B receives message.
- User B sends delivery ack.
- User A gets double tick.
- User A sends message while B is offline.
- B reconnects and receives undelivered message.

## 24. Implementation Order

Recommended build order:

1. Create repo/service skeletons.
2. Build shared Python common library.
3. Build User Service and Aurora schema.
4. Build Auth Service with Cognito integration.
5. Build Message Service with DynamoDB schema and conversation id logic.
6. Build Socket Manager WebSocket handlers.
7. Build Presence Cache integration.
8. Build Message Cache integration.
9. Build Message Outbox Worker.
10. Build Delivery Service and MSK consumer.
11. Build frontend chat UI.
12. Add CDK infrastructure.
13. Add CI/CD.
14. Add observability and alerts.

## 25. Known Tradeoffs

| Decision | Tradeoff |
| --- | --- |
| ECS Fargate instead of EKS | Simpler operations, less Kubernetes learning initially. |
| Auth Service over Cognito | Backend owns workflow, but still avoids custom password storage. |
| DynamoDB for messages | Excellent access-pattern scaling, but queries must be designed upfront. |
| Single Socket Manager initially | Simpler first implementation, but it is a bottleneck until scaled. |
| Outbox pattern | More implementation work, but prevents lost broker events after DB writes. |
| No Conversation Service | Simpler service graph, but conversation metadata stays in Message Service/DB. |

## 26. Open Items For Implementation

These should be finalized before coding:

- Exact AWS region.
- Local development auth strategy: real Cognito dev pool or mock JWT issuer.
- Whether `user_chats` table is included in phase 1.
- Whether WebSocket connect token is passed through query string or subprotocol.
- Exact message body size limit.
- Initial ECS desired task counts per service.
- Initial DynamoDB capacity mode: on-demand is recommended for phase 1.

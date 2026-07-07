# Multi-User Chat Application - High-Level Design

## 1. Context

This document defines the high-level design for a scalable multi-user chat application for an organization with around 1,000,000 employees.

The current phase covers:

- User registration
- User login/logout
- Listing/searching users
- One-to-one chat
- Real-time message delivery
- Message history
- Single tick for server-accepted messages
- Double tick for delivered messages

Chatbot support is deferred to a later phase.

## 2. Design Goals

- Use a microservices architecture.
- Keep service boundaries clear.
- Use durable storage as the source of truth for messages.
- Use a message broker to decouple message persistence from delivery.
- Use cache for online/offline presence and hot recent chat reads.
- Avoid a separate Conversation Service for now.
- Support only one conversation between any two users.
- Keep the design scalable without overcomplicating the first implementation.

## 3. High-Level Architecture

![High-Level Architecture](diagrams/high-level-architecture.svg)

### 3.1 Recommended Technology Stack

This design will use AWS managed services where they reduce operational complexity without weakening the microservice boundaries.

| Area | Recommended Technology | Notes |
| --- | --- | --- |
| Frontend | React + TypeScript + Next.js | Frontend framework can change later; this is the recommended default. |
| Frontend hosting | Amazon S3 + Amazon CloudFront | AWS Amplify is also acceptable for a simpler beginner-friendly setup. |
| API Gateway | Amazon API Gateway HTTP API + WebSocket API | Single public entry point for HTTP and WebSocket traffic. |
| Backend language/framework | Python + FastAPI | Main backend service stack. |
| Backend packaging | Docker | Each microservice is built as a Docker image. |
| Container registry | Amazon ECR | Stores Docker images. |
| Container runtime | Amazon ECS on AWS Fargate | Runs containers without managing servers or Kubernetes initially. |
| Kubernetes | Not used initially | Amazon EKS can be considered later if Kubernetes becomes a learning or platform requirement. |
| Auth provider | Amazon Cognito User Pools | Stores credentials, authenticates users, and issues JWT tokens. |
| Auth Service | Python FastAPI service | Thin backend facade/orchestrator over Cognito and User Service. |
| User DB | Amazon Aurora PostgreSQL | Stores user profile data and supports relational queries/search. |
| Message DB | Amazon DynamoDB | Stores high-volume chat messages by conversation. |
| Message broker | Amazon MSK Serverless | Kafka-compatible broker for async message delivery events. |
| Presence cache | Amazon ElastiCache for Redis OSS or Valkey | Tracks online/offline state with TTL. |
| Message cache | Amazon ElastiCache for Redis OSS or Valkey | Stores recent hot messages per conversation. |
| Service permissions | AWS IAM roles and policies | Grants each backend service only the AWS permissions it needs. |
| Secrets/config | AWS Secrets Manager + SSM Parameter Store | Stores database credentials, service config, and secrets. |
| Observability | Amazon CloudWatch + AWS X-Ray/OpenTelemetry | Logs, metrics, traces, alarms. |
| Infrastructure as Code | AWS CDK in Python | Defines AWS resources repeatably. |
| CI/CD | GitHub Actions or AWS CodePipeline | Builds, tests, pushes images, and deploys services. |

Recommended service implementation stack:

```text
Python 3.12+
FastAPI
Pydantic
Uvicorn/Gunicorn
boto3 / aioboto3
SQLAlchemy or SQLModel for Aurora PostgreSQL access
Docker
```

## 4. Service Responsibilities

### 4.1 API Gateway

The API Gateway is the external entry point for both HTTP APIs and WebSocket traffic.

Responsibilities:

- Route registration and login requests to Auth Service.
- Route user listing/search requests to User Service.
- Route message history requests to Message Service.
- Route or proxy WebSocket connections to Socket Manager.
- Validate authentication tokens for protected APIs.
- Apply request-level controls such as rate limiting and basic validation.

Clients should not connect directly to Socket Manager. Socket Manager is an internal service behind API Gateway.

### 4.2 Auth Service

Auth Service is a thin backend facade over Amazon Cognito. It owns the application-facing registration and login APIs, but it does not store passwords itself.

Responsibilities:

- Accept registration requests from API Gateway.
- Validate required registration fields.
- Create users in Amazon Cognito.
- Coordinate profile creation with User Service.
- Authenticate login requests by calling Cognito.
- Return Cognito-issued JWT tokens to the client.
- Handle registration rollback if profile creation fails after Cognito user creation.

Auth Service does not own a custom Auth DB initially. Cognito is the credential store and token issuer.

### 4.3 User Service

User Service owns user profile and discovery.

Responsibilities:

- Store user profile data.
- Enforce or coordinate username uniqueness.
- Store Cognito user id against the application user profile.
- Return paginated users excluding the logged-in user.
- Support user search.

For scale, the system should not expose a literal unbounded `getAllUsers` API. It should provide paginated listing and search.

User Service owns User DB.

In the recommended AWS stack, User DB is Amazon Aurora PostgreSQL.

### 4.4 Socket Manager

Socket Manager owns live WebSocket session handling behind API Gateway.

Responsibilities:

- Authenticate WebSocket connections.
- Maintain active user socket sessions.
- Receive real-time client events.
- Forward send-message requests to Message Service.
- Push received messages to connected clients.
- Push delivery-status updates to senders.
- Write online/offline state to Presence Cache.
- On user reconnect, call Message Service to fetch undelivered messages.

At this stage, the design can assume a single Socket Manager instance. However, Presence Cache keeps the design compatible with future horizontal scaling.

### 4.5 Message Service

Message Service owns durable message state.

Responsibilities:

- Accept send-message requests.
- Validate sender and recipient.
- Compute deterministic `conversation_id` for a user pair.
- Persist messages in Message DB.
- Mark messages as `SENT` after durable persistence.
- Publish `MessageCreated` events to the Message Broker.
- Fetch message history by `conversation_id`.
- Fetch undelivered messages for a user.
- Update message status to `DELIVERED` when Delivery Service reports successful delivery.
- Own reads and writes for Message Cache.

Message DB remains the source of truth. Message Cache is only an optimization.

In the recommended AWS stack, Message DB is Amazon DynamoDB and Message Cache is Amazon ElastiCache for Redis OSS or Valkey.

### 4.6 Delivery Service

Delivery Service owns async real-time delivery workflow.

Responsibilities:

- Consume `MessageCreated` events from Message Broker.
- Read Presence Cache to check whether the recipient is online.
- Ask Socket Manager to push messages to online recipients.
- Receive or process delivery acknowledgements.
- Notify Message Service to mark messages as `DELIVERED`.
- Trigger sender delivery-status updates through Socket Manager.
- Leave messages in `SENT` state when the recipient is offline.

### 4.7 Message Broker

Message Broker decouples message persistence from delivery.

In the recommended AWS stack, Message Broker is Amazon MSK Serverless.

Primary events:

- `MessageCreated`
- `MessageDelivered`

The critical ordering is:

```text
Message DB write succeeds -> MessageCreated event is published
```

The broker should not be the only durable location for messages.

## 5. Cache Usage

### 5.1 Presence Cache

Presence Cache tracks online/offline state.

Ownership rule:

```text
Socket Manager writes Presence Cache.
Delivery Service reads Presence Cache.
```

Example presence record:

```text
presence:{user_id}
- user_id
- socket_id
- connected_at
- last_seen_at
- status: ONLINE
- ttl
```

Presence Cache should have TTL-based expiry so stale connections do not remain online forever.

A separate Presence Service is not needed in the current design. It can be introduced later if presence logic becomes more complex or multiple services need to write presence state.

### 5.2 Message Cache

Message Cache accelerates hot chat reads.

Ownership rule:

```text
Message Service owns Message Cache reads and writes.
```

Example cache key:

```text
recent_messages:{conversation_id}
```

Use Message Cache for:

- recently active chat history
- last N messages for a conversation
- reducing Message DB reads when users open active chats

Do not use Message Cache for:

- durable message storage
- final delivery status source of truth
- guaranteed offline recovery

The write order should be:

```text
Message DB first, Message Cache second
```

If cache update fails, the system should continue using Message DB.

## 6. Data Model

### 6.1 User

```text
users
- user_id
- cognito_user_id
- username
- first_name
- last_name
- created_at
- updated_at
```

### 6.2 Auth Identity

```text
Amazon Cognito User Pool
- cognito_user_id
- username
- password credential managed by Cognito
- token/session metadata managed by Cognito
```

The application should not store password hashes in its own database initially. Cognito is the credential store and JWT issuer.

### 6.3 Message

```text
messages
- message_id
- conversation_id
- from_user_id
- to_user_id
- body
- status
- created_at
- delivered_at
```

### 6.4 Optional Chat Index

This is not a separate service. It is an optional Message DB table to support chat list views efficiently.

```text
chats
- conversation_id
- user_low_id
- user_high_id
- last_message_id
- updated_at
```

## 7. Conversation ID Strategy

The system does not need a separate Conversation Service because there is only one conversation between any two users.

Message Service computes a deterministic `conversation_id`:

```text
conversation_id = hash(min(userA_id, userB_id) + ":" + max(userA_id, userB_id))
```

This ensures both users always resolve to the same conversation.

Benefits:

- No duplicate one-to-one conversations.
- Message history can be queried by one stable key.
- No separate Conversation Service is required.
- The design remains simple while still supporting efficient lookups.

## 8. Message Status

Supported statuses:

```text
SENT
DELIVERED
```

Meaning:

- `SENT`: message was durably saved by Message Service. Sender sees single tick.
- `DELIVERED`: recipient client acknowledged receipt. Sender sees double tick.

A message should not be marked `DELIVERED` merely because the backend attempted to push it. The recipient client must acknowledge receipt.

## 9. Sequence Diagrams

### 9.1 User Registration

```text
Client -> API Gateway: Register username, password, first name, last name
API Gateway -> Auth Service: Register user
Auth Service -> Cognito: Create user credentials
Cognito -> Auth Service: Cognito user created
Auth Service -> User Service: Create user profile with cognito_user_id
User Service -> User DB: Save profile
User DB -> User Service: Profile created
User Service -> Auth Service: User profile created
Auth Service -> API Gateway: Registration success
API Gateway -> Client: Registration success
```

If User Service profile creation fails after Cognito user creation, Auth Service should disable/delete the Cognito user or retry profile creation through a recovery flow.

### 9.2 Login

```text
Client -> API Gateway: Login username, password
API Gateway -> Auth Service: Login
Auth Service -> Cognito: Authenticate user
Cognito -> Auth Service: JWT tokens
Auth Service -> API Gateway: Login success with JWT tokens
API Gateway -> Client: Login success with JWT tokens
```

### 9.3 User Connects Over WebSocket

![WebSocket Connect and Undelivered Message Sync](diagrams/reconnect-sequence.svg)

```text
Client -> API Gateway: Open WebSocket with auth token
API Gateway -> Socket Manager: Proxy WebSocket connection
Socket Manager -> Socket Manager: Validate token
Socket Manager -> Presence Cache: Set presence:{user_id} = ONLINE with TTL
Socket Manager -> Message Service: Get undelivered messages for user_id
Message Service -> Message DB: Query messages where to_user_id = user_id and status = SENT
Message DB -> Message Service: Undelivered messages
Message Service -> Socket Manager: Undelivered messages
Socket Manager -> API Gateway: Push undelivered messages
API Gateway -> Client: Push undelivered messages
```

### 9.4 Send Message to Online User

![Message Delivery Sequence](diagrams/message-delivery-sequence.svg)

```text
Sender Client -> API Gateway: Send WebSocket message to recipient
API Gateway -> Socket Manager: Proxy message event
Socket Manager -> Message Service: Send message request
Message Service -> Message Service: Compute conversation_id
Message Service -> Message DB: Save message with status SENT
Message DB -> Message Service: Message saved
Message Service -> Message Cache: Update recent_messages:{conversation_id}
Message Service -> Message Broker: Publish MessageCreated
Message Service -> Socket Manager: Message accepted with status SENT
Socket Manager -> API Gateway: Single tick
API Gateway -> Sender Client: Single tick

Message Broker -> Delivery Service: Consume MessageCreated
Delivery Service -> Presence Cache: Check recipient presence
Presence Cache -> Delivery Service: Recipient ONLINE
Delivery Service -> Socket Manager: Deliver message to recipient
Socket Manager -> API Gateway: Push message through WebSocket
API Gateway -> Recipient Client: Push message
Recipient Client -> API Gateway: Delivery acknowledgement
API Gateway -> Socket Manager: Delivery acknowledgement
Socket Manager -> Delivery Service: Delivery acknowledgement
Delivery Service -> Message Service: Mark message DELIVERED
Message Service -> Message DB: Update status to DELIVERED
Message Service -> Message Cache: Update cached message status
Message Service -> Delivery Service: Status updated
Delivery Service -> Socket Manager: Notify sender of delivery
Socket Manager -> API Gateway: Push delivery status
API Gateway -> Sender Client: Double tick
```

### 9.5 Send Message to Offline User

```text
Sender Client -> API Gateway: Send WebSocket message to offline recipient
API Gateway -> Socket Manager: Proxy message event
Socket Manager -> Message Service: Send message request
Message Service -> Message Service: Compute conversation_id
Message Service -> Message DB: Save message with status SENT
Message DB -> Message Service: Message saved
Message Service -> Message Broker: Publish MessageCreated
Message Service -> Socket Manager: Message accepted with status SENT
Socket Manager -> API Gateway: Single tick
API Gateway -> Sender Client: Single tick

Message Broker -> Delivery Service: Consume MessageCreated
Delivery Service -> Presence Cache: Check recipient presence
Presence Cache -> Delivery Service: Recipient OFFLINE or missing
Delivery Service -> Delivery Service: Leave message as SENT
```

### 9.6 Offline User Reconnects

```text
Recipient Client -> API Gateway: Reconnect WebSocket
API Gateway -> Socket Manager: Proxy WebSocket connection
Socket Manager -> Socket Manager: Validate token
Socket Manager -> Presence Cache: Set presence:{user_id} = ONLINE with TTL
Socket Manager -> Message Service: Get undelivered messages for user_id
Message Service -> Message DB: Query messages where to_user_id = user_id and status = SENT
Message DB -> Message Service: Undelivered messages
Message Service -> Socket Manager: Return undelivered messages
Socket Manager -> API Gateway: Push undelivered messages
API Gateway -> Recipient Client: Push undelivered messages
Recipient Client -> API Gateway: Delivery acknowledgement
API Gateway -> Socket Manager: Proxy delivery acknowledgement
Socket Manager -> Delivery Service: Delivery acknowledgement
Delivery Service -> Message Service: Mark messages DELIVERED
Message Service -> Message DB: Update statuses to DELIVERED
Message Service -> Delivery Service: Status updated
Delivery Service -> Socket Manager: Notify senders of delivery
Socket Manager -> API Gateway: Push delivery status
API Gateway -> Sender Client: Double tick if sender is online
```

### 9.7 Fetch Recent Chat History

```text
Client
  -> API Gateway: Open chat with user_id
  -> Message Service: Fetch recent messages
  -> Message Service: Compute conversation_id
  -> Message Cache: Read recent_messages:{conversation_id}

If cache hit:
  <- Message Cache: Recent messages
  <- Message Service: Recent messages
  <- API Gateway: Recent messages

If cache miss:
  -> Message DB: Fetch recent messages by conversation_id
  <- Message DB: Recent messages
  -> Message Cache: Hydrate recent_messages:{conversation_id}
  <- Message Service: Recent messages
  <- API Gateway: Recent messages
```

## 10. Key Design Decisions

| Decision | Rationale |
| --- | --- |
| Use AWS managed services | Reduces operational work while still supporting scale. |
| Use Python + FastAPI for backend services | Matches the preferred language and keeps service implementation straightforward. |
| Use Docker + ECS Fargate | Gives containerized microservices without Kubernetes complexity initially. |
| Use Cognito behind Auth Service | Avoids custom password handling while keeping backend-owned registration/login orchestration. |
| Do not maintain custom Auth DB initially | Cognito stores credentials and issues JWT tokens. |
| Use Message Broker | Decouples message persistence from delivery and helps absorb traffic spikes. |
| Use Amazon MSK Serverless | Provides Kafka-compatible async delivery without managing Kafka brokers directly. |
| Separate Message Service and Delivery Service | Keeps durable message ownership separate from async delivery workflow. |
| No Conversation Service | Only one conversation exists between any two users; Message Service can compute `conversation_id`. |
| Use Aurora PostgreSQL for User DB | User profile data benefits from relational constraints and query flexibility. |
| Use DynamoDB for Message DB | Chat messages are high-volume append/read data keyed by conversation. |
| Use ElastiCache for Presence Cache | Fast online/offline checks with TTL. |
| Use ElastiCache for Message Cache | Faster access to recent active chat messages. |
| DB before broker/cache | Message DB remains the durable source of truth. |
| Socket Manager writes presence | Keeps presence ownership clear. |
| Delivery Service reads presence | Enables fast online/offline delivery decisions. |
| Use IAM roles per service | Each service gets only the AWS permissions it needs. |

## 11. Future Enhancements

These are not required for the current phase but should be considered later:

- Multiple Socket Manager instances.
- Amazon EKS/Kubernetes if container orchestration requirements outgrow ECS Fargate or Kubernetes becomes a learning/platform goal.
- Presence Service if presence logic becomes complex.
- Group chat.
- Read receipts.
- Typing indicators.
- Push notifications.
- Attachments.
- Message search.
- Chatbot integration.
- Multi-device support.
- Rich user presence and last-seen privacy controls.

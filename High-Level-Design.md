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

```mermaid
flowchart LR
    Client["Web / Mobile Client"]
    Gateway["API Gateway"]

    Auth["Auth Service"]
    User["User Service"]
    Socket["Socket Manager"]
    Message["Message Service"]
    Delivery["Delivery Service"]

    AuthDB[("Auth DB")]
    UserDB[("User DB")]
    MessageDB[("Message DB")]
    Broker[["Message Broker"]]
    PresenceCache[("Presence Cache")]
    MessageCache[("Message Cache")]

    Client <-->|HTTPS| Gateway
    Client <-->|WebSocket| Socket

    Gateway --> Auth
    Gateway --> User
    Gateway --> Message

    Auth --> AuthDB
    User --> UserDB

    Socket --> PresenceCache
    Delivery --> PresenceCache

    Socket --> Message
    Message --> MessageDB
    Message --> MessageCache
    Message --> Broker

    Broker --> Delivery
    Delivery --> Socket
    Delivery --> Message
```

## 4. Service Responsibilities

### 4.1 API Gateway

The API Gateway is the external entry point for HTTP APIs.

Responsibilities:

- Route registration and login requests to Auth Service.
- Route user listing/search requests to User Service.
- Route message history requests to Message Service.
- Validate authentication tokens for protected APIs.
- Apply request-level controls such as rate limiting and basic validation.

WebSocket traffic is handled by the Socket Manager.

### 4.2 Auth Service

Auth Service owns identity and credential management.

Responsibilities:

- Register credentials.
- Validate login credentials.
- Issue authentication token/session.
- Logout or invalidate session.
- Store password hashes and auth metadata.

Auth Service owns Auth DB.

### 4.3 User Service

User Service owns user profile and discovery.

Responsibilities:

- Store user profile data.
- Enforce or coordinate username uniqueness.
- Return paginated users excluding the logged-in user.
- Support user search.

For scale, the system should not expose a literal unbounded `getAllUsers` API. It should provide paginated listing and search.

User Service owns User DB.

### 4.4 Socket Manager

Socket Manager owns live WebSocket connections.

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
- username
- first_name
- last_name
- created_at
- updated_at
```

### 6.2 Auth

```text
credentials
- user_id
- username
- password_hash
- created_at
- updated_at
```

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

```mermaid
sequenceDiagram
    participant C as "Client"
    participant G as "API Gateway"
    participant A as "Auth Service"
    participant U as "User Service"
    participant ADB as "Auth DB"
    participant UDB as "User DB"

    C->>G: Register username, password, first name, last name
    G->>A: Create credentials
    A->>ADB: Check username and save password hash
    ADB-->>A: Credentials created
    A->>U: Create user profile
    U->>UDB: Save profile
    UDB-->>U: Profile created
    U-->>A: User profile created
    A-->>G: Registration success
    G-->>C: Registration success
```

### 9.2 Login

```mermaid
sequenceDiagram
    participant C as "Client"
    participant G as "API Gateway"
    participant A as "Auth Service"
    participant ADB as "Auth DB"

    C->>G: Login username, password
    G->>A: Authenticate
    A->>ADB: Fetch credentials
    ADB-->>A: Password hash
    A-->>G: Auth token
    G-->>C: Login success with token
```

### 9.3 User Connects Over WebSocket

```mermaid
sequenceDiagram
    participant C as "Client"
    participant S as "Socket Manager"
    participant P as "Presence Cache"
    participant M as "Message Service"
    participant DB as "Message DB"

    C->>S: Open WebSocket with auth token
    S->>S: Validate token
    S->>P: Set presence:{user_id} = ONLINE with TTL
    S->>M: Get undelivered messages for user_id
    M->>DB: Query messages where to_user_id = user_id and status = SENT
    DB-->>M: Undelivered messages
    M-->>S: Undelivered messages
    S-->>C: Push undelivered messages
```

### 9.4 Send Message to Online User

```mermaid
sequenceDiagram
    participant A as "Sender Client"
    participant S as "Socket Manager"
    participant M as "Message Service"
    participant DB as "Message DB"
    participant MC as "Message Cache"
    participant B as "Message Broker"
    participant D as "Delivery Service"
    participant P as "Presence Cache"
    participant R as "Recipient Client"

    A->>S: Send message to recipient
    S->>M: Send message request
    M->>M: Compute conversation_id
    M->>DB: Save message with status SENT
    DB-->>M: Message saved
    M->>MC: Update recent_messages:{conversation_id}
    M->>B: Publish MessageCreated
    M-->>S: Message accepted with status SENT
    S-->>A: Single tick

    B-->>D: Consume MessageCreated
    D->>P: Check recipient presence
    P-->>D: Recipient ONLINE
    D->>S: Deliver message to recipient
    S-->>R: Push message
    R-->>S: Delivery acknowledgement
    S-->>D: Delivery acknowledgement
    D->>M: Mark message DELIVERED
    M->>DB: Update status to DELIVERED
    M->>MC: Update cached message status
    M-->>D: Status updated
    D->>S: Notify sender of delivery
    S-->>A: Double tick
```

### 9.5 Send Message to Offline User

```mermaid
sequenceDiagram
    participant A as "Sender Client"
    participant S as "Socket Manager"
    participant M as "Message Service"
    participant DB as "Message DB"
    participant B as "Message Broker"
    participant D as "Delivery Service"
    participant P as "Presence Cache"

    A->>S: Send message to offline recipient
    S->>M: Send message request
    M->>M: Compute conversation_id
    M->>DB: Save message with status SENT
    DB-->>M: Message saved
    M->>B: Publish MessageCreated
    M-->>S: Message accepted with status SENT
    S-->>A: Single tick

    B-->>D: Consume MessageCreated
    D->>P: Check recipient presence
    P-->>D: Recipient OFFLINE or missing
    D->>D: Leave message as SENT
```

### 9.6 Offline User Reconnects

```mermaid
sequenceDiagram
    participant R as "Recipient Client"
    participant S as "Socket Manager"
    participant P as "Presence Cache"
    participant M as "Message Service"
    participant DB as "Message DB"
    participant D as "Delivery Service"
    participant A as "Sender Client"

    R->>S: Reconnect WebSocket
    S->>S: Validate token
    S->>P: Set presence:{user_id} = ONLINE with TTL
    S->>M: Get undelivered messages for user_id
    M->>DB: Query messages where to_user_id = user_id and status = SENT
    DB-->>M: Undelivered messages
    M-->>S: Return undelivered messages
    S-->>R: Push undelivered messages
    R-->>S: Delivery acknowledgement
    S-->>D: Delivery acknowledgement
    D->>M: Mark messages DELIVERED
    M->>DB: Update statuses to DELIVERED
    M-->>D: Status updated
    D->>S: Notify senders of delivery
    S-->>A: Double tick if sender is online
```

### 9.7 Fetch Recent Chat History

```mermaid
sequenceDiagram
    participant C as "Client"
    participant G as "API Gateway"
    participant M as "Message Service"
    participant MC as "Message Cache"
    participant DB as "Message DB"

    C->>G: Open chat with user_id
    G->>M: Fetch recent messages
    M->>M: Compute conversation_id
    M->>MC: Read recent_messages:{conversation_id}
    alt Cache hit
        MC-->>M: Recent messages
        M-->>G: Recent messages
    else Cache miss
        M->>DB: Fetch recent messages by conversation_id
        DB-->>M: Recent messages
        M->>MC: Hydrate recent_messages:{conversation_id}
        M-->>G: Recent messages
    end
    G-->>C: Recent messages
```

## 10. Key Design Decisions

| Decision | Rationale |
| --- | --- |
| Use Message Broker | Decouples message persistence from delivery and helps absorb traffic spikes. |
| Separate Message Service and Delivery Service | Keeps durable message ownership separate from async delivery workflow. |
| No Conversation Service | Only one conversation exists between any two users; Message Service can compute `conversation_id`. |
| Use Presence Cache | Fast online/offline checks for delivery decisions. |
| Use Message Cache | Faster access to recent active chat messages. |
| DB before broker/cache | Message DB remains the durable source of truth. |
| Socket Manager writes presence | Keeps presence ownership clear. |
| Delivery Service reads presence | Enables fast online/offline delivery decisions. |

## 11. Future Enhancements

These are not required for the current phase but should be considered later:

- Multiple Socket Manager instances.
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

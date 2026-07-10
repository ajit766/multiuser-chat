from fastapi import WebSocket


class ConnectionManager:
    """Holds the live WebSocket for every connected user on THIS gateway
    instance. v1 runs exactly one Gateway Service instance, so an in-memory
    dict is safe. Running more than one instance would require sharing this
    state (e.g. Redis pub/sub) since a push for a user connected to instance
    B would otherwise never reach them via instance A."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        existing = self._connections.get(user_id)
        if existing is not None:
            await existing.close(code=4000, reason="Replaced by a new connection")
        self._connections[user_id] = websocket

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        if self._connections.get(user_id) is websocket:
            del self._connections[user_id]

    async def send_to_user(self, user_id: str, payload: dict) -> bool:
        websocket = self._connections.get(user_id)
        if websocket is None:
            return False
        await websocket.send_json(payload)
        return True


manager = ConnectionManager()

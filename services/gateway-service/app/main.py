from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect

from . import message_client, presence_client
from .auth import InvalidTokenError, decode_user_id, require_internal_key
from .connection_manager import manager
from .schemas import PushRequest

app = FastAPI(title="Gateway Service")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    try:
        user_id = decode_user_id(token)
    except InvalidTokenError:
        await websocket.close(code=4401, reason="Invalid or expired token")
        return

    await websocket.accept()
    await manager.connect(user_id, websocket)
    await presence_client.mark_online(user_id)

    # Catch up every conversation this user was offline for, in one shot,
    # rather than waiting for them to open each chat individually. Push a
    # status_update to each original sender directly via our own
    # connection_manager - no HTTP hop needed since we already hold their
    # sockets (if they're connected at all).
    newly_delivered = await message_client.catch_up_pending_messages(user_id)
    for message in newly_delivered:
        await manager.send_to_user(
            message["from_user_id"],
            {
                "type": "status_update",
                "data": {
                    "id": message["id"],
                    "to_user_id": message["to_user_id"],
                    "status": "DELIVERED",
                },
            },
        )

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await presence_client.mark_online(user_id)  # refresh presence TTL
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        await presence_client.mark_offline(user_id)


@app.post("/internal/push", dependencies=[Depends(require_internal_key)])
async def push_to_user(payload: PushRequest):
    delivered = await manager.send_to_user(payload.user_id, payload.payload)
    return {"delivered": delivered}

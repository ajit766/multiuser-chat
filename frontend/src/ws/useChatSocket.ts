import { useEffect, useRef } from "react";

export interface StatusUpdate {
  id: string;
  to_user_id: string;
  status: "SENT" | "DELIVERED";
}

export interface SocketEvent {
  type: "message" | "status_update" | "pong" | string;
  data?: unknown;
}

type EventHandler = (event: SocketEvent) => void;

const HEARTBEAT_MS = 30_000;

export function useChatSocket(token: string | null, onEvent: EventHandler) {
  // Keep the latest handler in a ref so the effect below doesn't need to
  // reconnect the socket every time the handler identity changes.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!token) return;

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws?token=${token}`);

    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
      }
    }, HEARTBEAT_MS);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data) as SocketEvent;
      if (data.type === "pong") return;
      onEventRef.current(data);
    };

    return () => {
      clearInterval(heartbeat);
      ws.close();
    };
  }, [token]);
}

import type { Message } from "../api/types";

export function MessageBubble({ message, isOwn }: { message: Message; isOwn: boolean }) {
  return (
    <div className={`bubble ${isOwn ? "own" : "other"}`}>
      <span className="bubble-text">{message.message}</span>
      {isOwn && (
        <span
          className={`bubble-tick ${message.status === "DELIVERED" ? "delivered" : ""}`}
          aria-label={message.status}
        >
          {message.status === "DELIVERED" ? "✓✓" : "✓"}
        </span>
      )}
    </div>
  );
}

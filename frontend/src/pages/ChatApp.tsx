import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Message, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { MessageBubble } from "../components/MessageBubble";
import { useChatSocket, type StatusUpdate } from "../ws/useChatSocket";

export function ChatApp() {
  const { user, token, logout } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Record<string, Message[]>>({});
  const [draft, setDraft] = useState("");

  useEffect(() => {
    if (!token) return;
    api
      .listUsers(token)
      .then((list) => {
        setUsers(list);
        setUsersError(null);
      })
      .catch((err) => {
        setUsersError(err instanceof ApiError ? err.message : "Failed to load users");
      });
  }, [token]);

  const appendMessage = useCallback((otherUserId: string, message: Message) => {
    setConversations((prev) => {
      const existing = prev[otherUserId] ?? [];
      if (existing.some((m) => m.id === message.id)) {
        return { ...prev, [otherUserId]: existing.map((m) => (m.id === message.id ? message : m)) };
      }
      return { ...prev, [otherUserId]: [...existing, message] };
    });
  }, []);

  const updateMessageStatus = useCallback(
    (otherUserId: string, messageId: string, status: Message["status"]) => {
      setConversations((prev) => {
        const existing = prev[otherUserId];
        if (!existing) return prev;
        return {
          ...prev,
          [otherUserId]: existing.map((m) => (m.id === messageId ? { ...m, status } : m)),
        };
      });
    },
    []
  );

  useChatSocket(token, (event) => {
    if (event.type === "message" && event.data) {
      const message = event.data as Message;
      const otherUserId = message.from_user_id === user?.id ? message.to_user_id : message.from_user_id;
      appendMessage(otherUserId, message);
    } else if (event.type === "status_update" && event.data) {
      // Pushed to the SENDER when Delivery Service confirms the recipient
      // got the message - this is what flips a sent message's tick from
      // single to double without needing a page reload.
      const update = event.data as StatusUpdate;
      updateMessageStatus(update.to_user_id, update.id, update.status);
    }
  });

  async function openChat(otherUserId: string) {
    setSelectedUserId(otherUserId);
    if (!token || conversations[otherUserId]) return;
    try {
      const history = await api.getConversation(token, otherUserId);
      setConversations((prev) => ({ ...prev, [otherUserId]: history }));
    } catch {
      // Leave it empty - reselecting the user will retry the fetch.
    }
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    if (!token || !selectedUserId || !draft.trim()) return;
    const text = draft;
    setDraft("");
    try {
      const message = await api.sendMessage(token, { to_user_id: selectedUserId, message: text });
      appendMessage(selectedUserId, message);
    } catch {
      setDraft(text); // don't lose what they typed if the send failed
    }
  }

  const activeMessages = selectedUserId ? conversations[selectedUserId] ?? [] : [];
  const selectedUser = useMemo(
    () => users.find((u) => u.id === selectedUserId) ?? null,
    [users, selectedUserId]
  );

  return (
    <div className="chat-app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span>
            {user?.first_name} {user?.last_name}
          </span>
          <button className="link" onClick={logout}>
            Log out
          </button>
        </div>
        {usersError && <div className="error">{usersError}</div>}
        <ul className="user-list">
          {users.map((u) => (
            <li key={u.id} className={u.id === selectedUserId ? "active" : ""}>
              <span>
                {u.first_name} {u.last_name} <small>@{u.username}</small>
              </span>
              <button onClick={() => openChat(u.id)}>Start Chat</button>
            </li>
          ))}
          {users.length === 0 && !usersError && <li className="empty">No other users yet</li>}
        </ul>
      </aside>
      <main className="chat-window">
        {!selectedUser && <div className="empty-state">Select a user to start chatting</div>}
        {selectedUser && (
          <>
            <div className="chat-header">
              {selectedUser.first_name} {selectedUser.last_name}
            </div>
            <div className="message-list">
              {activeMessages.map((m) => (
                <MessageBubble key={m.id} message={m} isOwn={m.from_user_id === user?.id} />
              ))}
            </div>
            <form className="composer" onSubmit={handleSend}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={`Message ${selectedUser.first_name}`}
              />
              <button type="submit">Send</button>
            </form>
          </>
        )}
      </main>
    </div>
  );
}

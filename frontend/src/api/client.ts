import type {
  LoginPayload,
  LoginResponse,
  Message,
  RegisterPayload,
  SendMessagePayload,
  User,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(path, { ...options, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response had no JSON body - fall back to statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  register: (payload: RegisterPayload) =>
    request<User>("/users", { method: "POST", body: JSON.stringify(payload) }),

  login: (payload: LoginPayload) =>
    request<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify(payload) }),

  logout: (token: string) => request<void>("/auth/logout", { method: "POST" }, token),

  listUsers: (token: string) => request<User[]>("/users", {}, token),

  sendMessage: (token: string, payload: SendMessagePayload) =>
    request<Message>("/messages", { method: "POST", body: JSON.stringify(payload) }, token),

  getConversation: (token: string, otherUserId: string) =>
    request<Message[]>(`/messages/${otherUserId}`, {}, token),
};

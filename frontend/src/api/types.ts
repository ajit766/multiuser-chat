export interface User {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Message {
  id: string;
  from_user_id: string;
  to_user_id: string;
  message: string;
  status: "SENT" | "DELIVERED";
  created_at: string;
  delivered_at: string | null;
}

export interface RegisterPayload {
  username: string;
  password: string;
  first_name: string;
  last_name: string;
}

export interface LoginPayload {
  username: string;
  password: string;
}

export interface SendMessagePayload {
  to_user_id: string;
  message: string;
}

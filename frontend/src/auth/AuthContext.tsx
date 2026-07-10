import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { LoginPayload, RegisterPayload, User } from "../api/types";

interface AuthState {
  token: string | null;
  user: User | null;
  login: (payload: LoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

const STORAGE_KEY = "chat.auth";

function loadStoredAuth(): { token: string; user: User } | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as { token: string; user: User };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const stored = loadStoredAuth();
  const [token, setToken] = useState<string | null>(stored?.token ?? null);
  const [user, setUser] = useState<User | null>(stored?.user ?? null);

  const login = useCallback(async (payload: LoginPayload) => {
    const response = await api.login(payload);
    setToken(response.access_token);
    setUser(response.user);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token: response.access_token, user: response.user })
    );
  }, []);

  const register = useCallback(
    async (payload: RegisterPayload) => {
      await api.register(payload);
      await login({ username: payload.username, password: payload.password });
    },
    [login]
  );

  const logout = useCallback(() => {
    if (token) {
      api.logout(token).catch(() => {
        // best-effort - v1 logout is client-side anyway
      });
    }
    setToken(null);
    setUser(null);
    localStorage.removeItem(STORAGE_KEY);
  }, [token]);

  return (
    <AuthContext.Provider value={{ token, user, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

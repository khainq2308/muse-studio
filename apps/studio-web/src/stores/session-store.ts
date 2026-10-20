import { create } from "zustand";

interface SessionState {
  /** Kept in memory only. Never enable Zustand persistence for this store. */
  adminToken: string;
  /** Non-secret version used to invalidate session-scoped query caches. */
  sessionVersion: number;
  setAdminToken: (token: string) => void;
  clearAdminToken: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  adminToken: "",
  sessionVersion: 0,
  setAdminToken: (token) =>
    set((state) => ({ adminToken: token.trim(), sessionVersion: state.sessionVersion + 1 })),
  clearAdminToken: () =>
    set((state) => ({ adminToken: "", sessionVersion: state.sessionVersion + 1 })),
}));

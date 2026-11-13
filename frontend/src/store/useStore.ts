import { create } from 'zustand';

export interface User {
  id: number;
  username: string;
  email: string;
  created_at?: string;
}

export interface AppState {
  user: User | null;
  currentWorkflowId: number | null;
  isDarkMode: boolean;
  setUser: (user: User | null) => void;
  clearUser: () => void;
  setCurrentWorkflow: (workflowId: number | null) => void;
}

export const useStore = create<AppState>((set) => ({
  user: null,
  currentWorkflowId: null,
  isDarkMode: false,
  setUser: (user) => set({ user }),
  clearUser: () => set({ user: null }),
  setCurrentWorkflow: (currentWorkflowId) => set({ currentWorkflowId }),
}));

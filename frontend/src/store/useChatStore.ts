import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import {
  appendChatMessage,
  applyChatResponse,
  createChatSession,
  mergeChatSessions,
  mostRecentSessionWithMessages,
  setChatSessionInitialInput,
  setChatSessionWorkflow,
  type ChatMessageInput,
  type ChatSessionRecord,
} from '../lib/chat-history';
import {
  deleteChatSession,
  listChatSessions,
  saveChatSession,
} from '../api/chatSessions';
import type { ChatResponse } from '../types/workflow';

interface ChatStoreState {
  sessions: ChatSessionRecord[];
  activeSessionId: string | null;
  ensureSession: () => string;
  createSession: () => string;
  selectSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  appendMessage: (
    sessionId: string,
    message: ChatMessageInput,
  ) => void;
  applyResponse: (
    sessionId: string,
    response: ChatResponse,
  ) => void;
  setWorkflow: (sessionId: string, workflowId: number) => void;
  setInitialInput: (sessionId: string, input: string) => void;
  hydrateFromServer: () => Promise<void>;
}

function persistSession(session: ChatSessionRecord | undefined): void {
  if (!session) {
    return;
  }

  void saveChatSession(session).catch(() => {
    // Local state remains available while the backend reconnects.
  });
}

function updateSession(
  sessions: ChatSessionRecord[],
  sessionId: string,
  update: (session: ChatSessionRecord) => ChatSessionRecord,
): ChatSessionRecord[] {
  return sessions.map((session) =>
    session.id === sessionId ? update(session) : session,
  );
}

export const useChatStore = create<ChatStoreState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: null,
      ensureSession: () => {
        const state = get();
        const activeExists = state.sessions.some(
          (session) => session.id === state.activeSessionId,
        );
        if (activeExists && state.activeSessionId) {
          return state.activeSessionId;
        }

        const existingSession = state.sessions[0];
        if (existingSession) {
          set({ activeSessionId: existingSession.id });
          return existingSession.id;
        }

        return get().createSession();
      },
      createSession: () => {
        const session = createChatSession();
        set((state) => ({
          sessions: [session, ...state.sessions],
          activeSessionId: session.id,
        }));
        persistSession(session);
        return session.id;
      },
      selectSession: (sessionId) => {
        set({ activeSessionId: sessionId });
      },
      deleteSession: (sessionId) => {
        set((state) => {
          const sessions = state.sessions.filter(
            (session) => session.id !== sessionId,
          );
          const activeSessionId =
            state.activeSessionId === sessionId
              ? sessions[0]?.id ?? null
              : state.activeSessionId;

          return { sessions, activeSessionId };
        });
        void deleteChatSession(sessionId).catch(() => {
          // Keep the local deletion; the next hydration will reconcile.
        });
      },
      appendMessage: (sessionId, message) => {
        set((state) => ({
          sessions: updateSession(
            state.sessions,
            sessionId,
            (session) => appendChatMessage(session, message),
          ),
        }));
        persistSession(
          get().sessions.find((session) => session.id === sessionId),
        );
      },
      applyResponse: (sessionId, response) => {
        set((state) => ({
          sessions: updateSession(
            state.sessions,
            sessionId,
            (session) => applyChatResponse(session, response),
          ),
        }));
        persistSession(
          get().sessions.find((session) => session.id === sessionId),
        );
      },
      setWorkflow: (sessionId, workflowId) => {
        set((state) => ({
          sessions: updateSession(
            state.sessions,
            sessionId,
            (session) =>
              setChatSessionWorkflow(session, workflowId),
          ),
        }));
        persistSession(
          get().sessions.find((session) => session.id === sessionId),
        );
      },
      setInitialInput: (sessionId, input) => {
        set((state) => ({
          sessions: updateSession(
            state.sessions,
            sessionId,
            (session) =>
              setChatSessionInitialInput(session, input),
          ),
        }));
        persistSession(
          get().sessions.find((session) => session.id === sessionId),
        );
      },
      hydrateFromServer: async () => {
        try {
          const remoteSessions = await listChatSessions();
          const merged = mergeChatSessions(
            get().sessions,
            remoteSessions,
          );
          const currentActive = get().activeSessionId;
          const active = merged.find(
            (session) => session.id === currentActive,
          );
          const fallback = mostRecentSessionWithMessages(merged);
          const activeSessionId =
            active && active.messages.length > 0
              ? active.id
              : fallback?.id ?? active?.id ?? merged[0]?.id ?? null;

          set({
            sessions: merged,
            activeSessionId,
          });

          await Promise.all(
            merged.map((session) =>
              saveChatSession(session).catch(() => session),
            ),
          );
        } catch {
          // The local cache remains the fallback during startup.
        }
      },
    }),
    {
      name: 'nl-rpa-chat-sessions',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
      }),
    },
  ),
);

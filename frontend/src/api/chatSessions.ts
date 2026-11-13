import api from './axios';
import type { ChatSessionRecord } from '../lib/chat-history';

interface ChatSessionApiRecord {
  session_id: string;
  title: string;
  messages: ChatSessionRecord['messages'];
  structured_plan: ChatSessionRecord['structuredPlan'];
  generated_code: string | null;
  needs_confirmation: boolean;
  active_workflow_id: number | null;
  initial_input: string;
  updated_at: string;
}

function normalizeTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toISOString();
}

function toRecord(record: ChatSessionApiRecord): ChatSessionRecord {
  return {
    id: record.session_id,
    title: record.title,
    messages: record.messages ?? [],
    structuredPlan: record.structured_plan,
    generatedCode: record.generated_code,
    needsConfirmation: record.needs_confirmation,
    activeWorkflowId: record.active_workflow_id,
    initialInput: record.initial_input,
    updatedAt: normalizeTimestamp(record.updated_at),
  };
}

export async function listChatSessions(): Promise<ChatSessionRecord[]> {
  const response = await api.get<ChatSessionApiRecord[]>(
    '/api/chat/sessions',
  );
  return response.data.map(toRecord);
}

export async function saveChatSession(
  session: ChatSessionRecord,
): Promise<ChatSessionRecord> {
  const response = await api.put<ChatSessionApiRecord>(
    `/api/chat/sessions/${encodeURIComponent(session.id)}`,
    {
      title: session.title,
      messages: session.messages,
      structured_plan: session.structuredPlan,
      generated_code: session.generatedCode,
      needs_confirmation: session.needsConfirmation,
      active_workflow_id: session.activeWorkflowId,
      initial_input: session.initialInput,
      updated_at: session.updatedAt,
    },
  );
  return toRecord(response.data);
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  await api.delete(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}`,
  );
}

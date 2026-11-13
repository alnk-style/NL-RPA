import type { ChatResponse, StructuredPlan } from '../types/workflow';

export interface ChatHistoryMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

export interface ChatSessionRecord {
  id: string;
  title: string;
  messages: ChatHistoryMessage[];
  structuredPlan: StructuredPlan | null;
  generatedCode: string | null;
  needsConfirmation: boolean;
  activeWorkflowId: number | null;
  initialInput: string;
  updatedAt: string;
}

export interface ChatMessageInput {
  id?: string;
  role: ChatHistoryMessage['role'];
  content: string;
  timestamp?: string;
}

const DEFAULT_SESSION_TITLE = '新会话';
const MAX_SESSION_TITLE_LENGTH = 24;

function createChatId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function currentTime(): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date());
}

function buildTitle(content: string): string {
  const normalized = content.trim().replace(/\s+/g, ' ');
  if (!normalized) {
    return DEFAULT_SESSION_TITLE;
  }

  if (normalized.length <= MAX_SESSION_TITLE_LENGTH) {
    return normalized;
  }

  return `${normalized.slice(0, MAX_SESSION_TITLE_LENGTH)}...`;
}

export function createChatSession(
  sessionId = createChatId(),
  now = new Date().toISOString(),
): ChatSessionRecord {
  return {
    id: sessionId,
    title: DEFAULT_SESSION_TITLE,
    messages: [],
    structuredPlan: null,
    generatedCode: null,
    needsConfirmation: false,
    activeWorkflowId: null,
    initialInput: '',
    updatedAt: now,
  };
}

export function appendChatMessage(
  session: ChatSessionRecord,
  message: ChatMessageInput,
  now = new Date().toISOString(),
): ChatSessionRecord {
  const content = message.content.trim();
  const nextMessage: ChatHistoryMessage = {
    id: message.id ?? createChatId(),
    role: message.role,
    content,
    timestamp: message.timestamp ?? currentTime(),
  };
  const title =
    session.messages.length === 0 && message.role === 'user'
      ? buildTitle(content)
      : session.title;

  return {
    ...session,
    title,
    messages: [...session.messages, nextMessage],
    updatedAt: now,
  };
}

export function applyChatResponse(
  session: ChatSessionRecord,
  response: ChatResponse,
  now = new Date().toISOString(),
): ChatSessionRecord {
  const structuredPlan =
    response.structured_plan ?? session.structuredPlan;
  const nextPlan = structuredPlan ? { ...structuredPlan } : null;
  if (
    nextPlan &&
    nextPlan.executor_type === undefined &&
    response.executor_type
  ) {
    nextPlan.executor_type = response.executor_type;
  }

  return {
    ...session,
    structuredPlan: nextPlan,
    generatedCode: response.generated_code ?? session.generatedCode,
    needsConfirmation: Boolean(
      response.structured_plan && response.needs_confirmation,
    ),
    updatedAt: now,
  };
}

export function setChatSessionWorkflow(
  session: ChatSessionRecord,
  workflowId: number,
  now = new Date().toISOString(),
): ChatSessionRecord {
  return {
    ...session,
    activeWorkflowId: workflowId,
    updatedAt: now,
  };
}

export function setChatSessionInitialInput(
  session: ChatSessionRecord,
  input: string,
  now = new Date().toISOString(),
): ChatSessionRecord {
  if (session.initialInput) {
    return session;
  }

  return {
    ...session,
    initialInput: input,
    updatedAt: now,
  };
}

export function mergeChatSessions(
  localSessions: ChatSessionRecord[],
  remoteSessions: ChatSessionRecord[],
): ChatSessionRecord[] {
  const merged = new Map<string, ChatSessionRecord>();

  for (const session of remoteSessions) {
    merged.set(session.id, session);
  }
  for (const session of localSessions) {
    const existing = merged.get(session.id);
    if (!existing || session.updatedAt > existing.updatedAt) {
      merged.set(session.id, session);
    }
  }

  return [...merged.values()].sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt),
  );
}

export function mostRecentSessionWithMessages(
  sessions: ChatSessionRecord[],
): ChatSessionRecord | null {
  return (
    [...sessions]
      .filter((session) => session.messages.length > 0)
      .sort((left, right) =>
        right.updatedAt.localeCompare(left.updatedAt),
      )[0] ?? null
  );
}

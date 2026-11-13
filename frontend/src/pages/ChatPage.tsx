import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import {
  ArrowUpRight,
  Check,
  ClipboardList,
  History,
  LoaderCircle,
  Pencil,
  Plus,
  Send,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { WorkflowDagPreview } from '../components/WorkflowDagPreview';
import { MCPPlanPreview } from '../components/chat/MCPPlanPreview';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { ChatBubble } from '../components/ui/ChatBubble';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../components/ui/Card';
import * as chatApi from '../api/chat';
import * as mcpApi from '../api/mcp';
import * as workflowApi from '../api/workflows';
import { getApiErrorMessage } from '../lib/errors';
import {
  executorLabel,
  getPlanExecutorType,
} from '../lib/chat-plan';
import { buildWorkflowGraph, buildWorkflowName } from '../lib/workflow-graph';
import { saveWorkflowDraft } from '../lib/workflow-draft';
import { useChatStore } from '../store/useChatStore';
import { useStore } from '../store/useStore';
import type {
  ChatResponse,
  StructuredPlan,
} from '../types/workflow';
import type { MCPToolCatalogItem } from '../types/mcp';

const WELCOME_MESSAGE = [
  '你好，我是 NL-RPA 流程助手。',
  '',
  '请用自然语言描述你想自动化的任务，我会生成流程步骤和可执行代码。',
].join('\n');

const QUICK_PROMPTS = [
  '每天早上9点登录网银导出流水，筛选金额大于1000元的交易并发送邮件',
  '每小时检查一次订单后台，提取待发货订单并生成表格',
  '登录企业系统，下载本月发票并汇总发送给财务',
];

function currentTime(): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date());
}

function responseMarkdown(response: ChatResponse): string {
  const sections = [response.reply?.trim() || '流程已更新。'];

  if (response.generated_code?.trim()) {
    sections.push(
      [
        '',
        '```python',
        response.generated_code.trim(),
        '```',
      ].join('\n'),
    );
  }

  return sections.join('\n');
}

function QuickPrompts({
  disabled,
  onSelect,
}: {
  disabled: boolean;
  onSelect: (prompt: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-medium text-slate-400 dark:text-slate-500">
        <Sparkles className="h-3.5 w-3.5" />
        <span>快捷描述</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {QUICK_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(prompt)}
            className="group flex min-h-20 items-start justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-3 text-left text-xs leading-5 text-slate-600 transition-colors hover:border-brand-300 hover:bg-brand-50 hover:text-brand-800 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-brand-700 dark:hover:bg-brand-900/30 dark:hover:text-brand-100"
          >
            <span>{prompt}</span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-300 transition-colors group-hover:text-brand-500 dark:text-slate-600" />
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChatPage() {
  const navigate = useNavigate();
  const setCurrentWorkflow = useStore(
    (state) => state.setCurrentWorkflow,
  );
  const sessions = useChatStore((state) => state.sessions);
  const activeSessionId = useChatStore(
    (state) => state.activeSessionId,
  );
  const ensureSession = useChatStore((state) => state.ensureSession);
  const createSession = useChatStore((state) => state.createSession);
  const selectSession = useChatStore((state) => state.selectSession);
  const deleteSession = useChatStore((state) => state.deleteSession);
  const storeAppendMessage = useChatStore(
    (state) => state.appendMessage,
  );
  const storeApplyResponse = useChatStore(
    (state) => state.applyResponse,
  );
  const setSessionWorkflow = useChatStore(
    (state) => state.setWorkflow,
  );
  const setSessionInitialInput = useChatStore(
    (state) => state.setInitialInput,
  );
  const hydrateSessions = useChatStore(
    (state) => state.hydrateFromServer,
  );
  const activeSession =
    sessions.find((session) => session.id === activeSessionId) ?? null;
  const messages = activeSession?.messages ?? [];
  const structuredPlan = activeSession?.structuredPlan ?? null;
  const generatedCode = activeSession?.generatedCode ?? null;
  const needsConfirmation =
    activeSession?.needsConfirmation ?? false;
  const activeWorkflowId = activeSession?.activeWorkflowId ?? null;
  const [input, setInput] = useState('');
  const [awaitingModification, setAwaitingModification] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [mcpCatalog, setMcpCatalog] = useState<MCPToolCatalogItem[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let cancelled = false;
    void hydrateSessions().finally(() => {
      if (!cancelled) {
        setHistoryReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [hydrateSessions]);

  useEffect(() => {
    let active = true;
    void mcpApi.listMCPCatalog(undefined, 50)
      .then((payload) => {
        if (active) {
          setMcpCatalog(payload.tools);
        }
      })
      .catch(() => {
        if (active) {
          setMcpCatalog([]);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (historyReady && !activeSessionId) {
      ensureSession();
    }
  }, [activeSessionId, ensureSession, historyReady]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isSending]);

  function ensureSessionId(): string {
    if (
      activeSessionId &&
      sessions.some((session) => session.id === activeSessionId)
    ) {
      return activeSessionId;
    }

    return ensureSession();
  }

  function appendMessage(
    sessionId: string,
    role: 'user' | 'assistant',
    content: string,
  ) {
    storeAppendMessage(sessionId, { role, content });
  }

  function applyResponse(sessionId: string, response: ChatResponse) {
    storeApplyResponse(sessionId, response);
  }

  async function saveDraft(
    sessionId: string,
    plan: StructuredPlan,
    code: string | null,
  ): Promise<number> {
    const naturalLanguageInput =
      activeSession?.initialInput || '通过对话生成的自动化流程';
    const executorType = getPlanExecutorType(plan);

    const saved = await saveWorkflowDraft(workflowApi, {
      workflowId: activeWorkflowId,
      createPayload: {
        name: buildWorkflowName(naturalLanguageInput),
        description: '通过对话生成的流程',
        natural_language_input: naturalLanguageInput,
        executor_type: executorType,
        required_capabilities: plan.required_capabilities ?? [],
      },
      updatePayload: {
        structured_plan: plan,
        generated_code: code,
        dag_definition: buildWorkflowGraph(plan) as unknown as Record<
          string,
          unknown
        >,
        trigger_type: plan.trigger?.type === 'cron' ? 'cron' : 'manual',
        cron_expression:
          typeof plan.trigger?.cron === 'string'
            ? plan.trigger.cron
            : null,
        status: 'draft',
        executor_type: executorType,
        required_capabilities: plan.required_capabilities ?? [],
      },
    });
    const workflowId = saved.id;

    setCurrentWorkflow(workflowId);
    setSessionWorkflow(sessionId, workflowId);
    return workflowId;
  }

  async function handleInitialMessage(message: string) {
    const trimmed = message.trim();
    if (!trimmed || isSending || isSaving) {
      return;
    }

    const sessionId = ensureSessionId();
    setSessionInitialInput(sessionId, trimmed);
    setInput('');
    setRequestError(null);
    appendMessage(sessionId, 'user', trimmed);
    setIsSending(true);

    try {
      const response = await chatApi.sendChatMessage({
        session_id: sessionId,
        message: trimmed,
        workflow_id: null,
      });
      applyResponse(sessionId, response);
      appendMessage(sessionId, 'assistant', responseMarkdown(response));
    } catch (error) {
      const message = getApiErrorMessage(
        error,
        '生成流程失败，请稍后重试。',
      );
      setRequestError(message);
      appendMessage(sessionId, 'assistant', `生成失败：${message}`);
    } finally {
      setIsSending(false);
    }
  }

  async function handleModification(message: string) {
    if (!structuredPlan) {
      return;
    }

    const trimmed = message.trim();
    if (!trimmed || isSending || isSaving) {
      return;
    }

    const sessionId = ensureSessionId();
    setInput('');
    setRequestError(null);
    appendMessage(sessionId, 'user', trimmed);
    setIsSending(true);

    try {
      const workflowId =
        activeWorkflowId ??
        (await saveDraft(sessionId, structuredPlan, generatedCode));
      const response = await chatApi.modifyWorkflow({
        session_id: sessionId,
        modification: trimmed,
        workflow_id: workflowId,
      });
      applyResponse(sessionId, response);
      appendMessage(sessionId, 'assistant', responseMarkdown(response));
      setAwaitingModification(false);
    } catch (error) {
      const message = getApiErrorMessage(
        error,
        '修改流程失败，请稍后重试。',
      );
      setRequestError(message);
      appendMessage(sessionId, 'assistant', `修改失败：${message}`);
    } finally {
      setIsSending(false);
    }
  }

  async function handleStartModification() {
    if (!structuredPlan || isSaving || isSending) {
      return;
    }

    setRequestError(null);
    setIsSaving(true);

    try {
      const sessionId = ensureSessionId();
      const workflowId =
        activeWorkflowId ??
        (await saveDraft(sessionId, structuredPlan, generatedCode));
      setSessionWorkflow(sessionId, workflowId);
      setAwaitingModification(true);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (error) {
      setRequestError(
        getApiErrorMessage(error, '暂存流程失败，请稍后重试。'),
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function handleConfirm() {
    if (!structuredPlan || isSaving || isSending) {
      return;
    }

    setRequestError(null);
    setIsSaving(true);

    try {
      const sessionId = ensureSessionId();
      const workflowId =
        activeWorkflowId ??
        (await saveDraft(sessionId, structuredPlan, generatedCode));

      await chatApi.confirmWorkflow({
        session_id: sessionId,
        workflow_id: workflowId,
      });

      setCurrentWorkflow(workflowId);
      navigate(`/workflows/${workflowId}`);
    } catch (error) {
      setRequestError(
        getApiErrorMessage(error, '保存流程失败，请稍后重试。'),
      );
    } finally {
      setIsSaving(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (awaitingModification) {
      void handleModification(input);
    } else {
      void handleInitialMessage(input);
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function handleSelectSession(sessionId: string) {
    selectSession(sessionId);
    setAwaitingModification(false);
    setInput('');
    setRequestError(null);
  }

  function handleCreateSession() {
    createSession();
    setAwaitingModification(false);
    setInput('');
    setRequestError(null);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function handleDeleteSession() {
    if (!activeSessionId) {
      return;
    }

    deleteSession(activeSessionId);
    setAwaitingModification(false);
    setInput('');
    setRequestError(null);
  }

  const composerLocked =
    isSending || isSaving || (needsConfirmation && !awaitingModification);
  const sessionOptions = [...sessions].sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt),
  );
  const planExecutor = getPlanExecutorType(structuredPlan);

  return (
    <section className="grid min-h-[calc(100vh-8rem)] gap-4 lg:h-[calc(100vh-4rem)] lg:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
      <Card className="flex min-h-[620px] flex-col overflow-hidden lg:h-full">
        <CardHeader className="border-b border-slate-100 py-4 dark:border-slate-800">
          <div className="space-y-3">
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-50 text-brand-600 dark:bg-brand-900/40 dark:text-brand-100">
                    <ClipboardList className="h-4 w-4" />
                  </span>
                  对话交互
                </CardTitle>
                <CardDescription className="mt-1">
                  描述你的自动化目标，助手会整理成可确认的流程方案。
                </CardDescription>
              </div>
              <Badge variant={structuredPlan ? 'success' : 'secondary'}>
                {structuredPlan ? '已有方案' : '等待需求'}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 shrink-0 text-slate-400" />
              <select
                value={activeSessionId ?? ''}
                onChange={(event) =>
                  handleSelectSession(event.target.value)
                }
                className="min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                aria-label="历史会话"
              >
                {sessionOptions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleCreateSession}
                leftIcon={<Plus className="h-3.5 w-3.5" />}
              >
                新会话
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleDeleteSession}
                disabled={!activeSessionId}
                aria-label="删除当前会话"
                title="删除当前会话"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </CardHeader>

        <div
          className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5"
          aria-live="polite"
        >
          <ChatBubble
            role="assistant"
            content={WELCOME_MESSAGE}
            timestamp={currentTime()}
          />

          {messages.map((message) => (
            <ChatBubble
              key={message.id}
              role={message.role}
              content={message.content}
              timestamp={message.timestamp}
            />
          ))}

          {messages.length === 0 ? (
            <QuickPrompts
              disabled={composerLocked}
              onSelect={(prompt) => {
                void handleInitialMessage(prompt);
              }}
            />
          ) : null}

          {isSending ? (
            <div className="flex items-center gap-3 pl-11 text-sm text-slate-500 dark:text-slate-400">
              <LoaderCircle className="h-4 w-4 animate-spin text-brand-500" />
              正在生成流程方案...
            </div>
          ) : null}

          <div ref={messagesEndRef} />
        </div>

        {needsConfirmation && structuredPlan ? (
          <div className="border-t border-slate-100 bg-slate-50/80 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/40">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant="warning">
                    {awaitingModification ? '修改模式' : '待确认'}
                  </Badge>
                  <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                    {awaitingModification
                      ? '请输入修正指令，例如“把阈值改成5000”'
                      : '流程方案已生成，确认后保存为草稿'}
                  </p>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {awaitingModification
                    ? '发送后会重新生成流程图和代码。'
                    : `当前方案包含 ${structuredPlan.steps?.length ?? 0} 个步骤。`}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {awaitingModification ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setAwaitingModification(false);
                      setInput('');
                    }}
                    disabled={isSending || isSaving}
                  >
                    取消修改
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    leftIcon={<Pencil className="h-3.5 w-3.5" />}
                    onClick={() => {
                      void handleStartModification();
                    }}
                    disabled={isSending || isSaving}
                  >
                    修改
                  </Button>
                )}
                <Button
                  size="sm"
                  leftIcon={
                    isSaving ? (
                      <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )
                  }
                  onClick={() => {
                    void handleConfirm();
                  }}
                  disabled={isSending || isSaving || awaitingModification}
                >
                  确认并保存
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        <form
          className="border-t border-slate-100 p-4 dark:border-slate-800"
          onSubmit={handleSubmit}
        >
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleInputKeyDown}
              rows={2}
              disabled={composerLocked}
              placeholder={
                awaitingModification
                  ? '输入修改指令，例如：把阈值改成5000'
                  : needsConfirmation
                    ? '请先确认或修改当前方案'
                    : '描述你的自动化需求，例如：每天早上9点登录网银导出流水...'
              }
              className="min-h-[52px] flex-1 resize-none rounded-md border border-slate-200 bg-white px-3 py-2.5 text-sm leading-5 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:disabled:bg-slate-800"
              aria-label="流程需求输入"
            />
            <Button
              type="submit"
              size="lg"
              className="h-[52px] shrink-0 px-4"
              disabled={composerLocked || !input.trim()}
              aria-label="发送"
            >
              {isSending ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              <span className="hidden sm:inline">
                {awaitingModification ? '提交修改' : '发送'}
              </span>
            </Button>
          </div>
          {requestError ? (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">
              {requestError}
            </p>
          ) : (
            <p className="mt-2 text-xs text-slate-400">
              Enter 发送，Shift + Enter 换行
            </p>
          )}
        </form>
      </Card>

      <Card className="flex min-h-[480px] flex-col overflow-hidden lg:h-full">
        <CardHeader className="border-b border-slate-100 py-4 dark:border-slate-800">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>流程预览</CardTitle>
              <CardDescription className="mt-1">
                生成后的节点和执行顺序会实时显示。
              </CardDescription>
            </div>
            <Badge variant={structuredPlan ? 'default' : 'outline'}>
              {structuredPlan
                ? `${executorLabel(planExecutor)} · ${structuredPlan.steps?.length ?? 0} 个步骤`
                : '等待生成'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-3">
          <MCPPlanPreview plan={structuredPlan} catalog={mcpCatalog} />
          <WorkflowDagPreview
            plan={structuredPlan}
            className="min-h-[320px] flex-1"
          />
        </CardContent>
      </Card>
    </section>
  );
}

export default ChatPage;

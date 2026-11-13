import { useEffect, useMemo, useState } from 'react';
import {
  Clipboard,
  Check,
  Crosshair,
  LoaderCircle,
  RefreshCw,
  Square,
  Trash2,
  TriangleAlert,
  Video,
  X,
} from 'lucide-react';

import {
  discardRecording,
  finishRecording,
  getRecording,
  pickRecordingElement,
  recordingApiBaseUrl,
  startRecording,
} from '../../api/recordings';
import { getApiErrorMessage } from '../../lib/errors';
import { cn } from '../../lib/utils';
import {
  describeRecordingStep,
  mergeRecordingDraft,
  recordingCommand,
} from '../../lib/workflow-recording';
import { Button } from '../ui/Button';
import type {
  RecordingMode,
  RecordingSessionRecord,
  StructuredPlan,
} from '../../types/workflow';

export interface WorkflowRecordingPanelProps {
  workflowId: number;
  plan: StructuredPlan;
  onApplyDraft: (plan: StructuredPlan) => void;
  onNotice?: (notice: {
    tone: 'success' | 'error';
    message: string;
  }) => void;
  onClose: () => void;
}

type BusyAction =
  | 'start'
  | 'finish'
  | 'pick'
  | 'refresh'
  | 'discard'
  | null;

function firstRecordedUrl(plan: StructuredPlan): string | null {
  for (const step of plan.steps ?? []) {
    const params = step.params ?? {};
    const value = params.url ?? params.href;
    if (typeof value === 'string' && /^https?:/i.test(value.trim())) {
      return value.trim();
    }
  }
  return null;
}

export function WorkflowRecordingPanel({
  workflowId,
  plan,
  onApplyDraft,
  onNotice,
  onClose,
}: WorkflowRecordingPanelProps) {
  const [mode, setMode] = useState<RecordingMode>('web');
  const [session, setSession] = useState<RecordingSessionRecord | null>(
    null,
  );
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const startUrl = useMemo(() => firstRecordedUrl(plan), [plan]);
  const isRecording = session?.status === 'recording';
  const command = useMemo(
    () =>
      session
        ? recordingCommand({
            baseUrl: recordingApiBaseUrl(),
            token: localStorage.getItem('token'),
            recordingId: session.id,
            startUrl,
          })
        : '',
    [session, startUrl],
  );

  useEffect(() => {
    if (!session || session.status !== 'recording') {
      return;
    }
    const recordingId = session.id;
    let active = true;
    const timer = window.setInterval(() => {
      void getRecording(recordingId)
        .then((next) => {
          if (active) {
            setSession(next);
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [session?.id, session?.status]);

  async function handleStart() {
    setBusy('start');
    setError(null);
    try {
      const created = await startRecording({
        mode,
        name: `录制 ${new Date().toLocaleString()}`,
        workflow_id: workflowId,
        start_url: startUrl ?? undefined,
      });
      setSession(created);
      onNotice?.({
        tone: 'success',
        message: '录制会话已开始。',
      });
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '开始录制失败。'),
      );
    } finally {
      setBusy(null);
    }
  }

  async function handleRefresh() {
    if (!session) {
      return;
    }
    setBusy('refresh');
    setError(null);
    try {
      setSession(await getRecording(session.id));
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '刷新录制状态失败。'),
      );
    } finally {
      setBusy(null);
    }
  }

  async function handleFinish() {
    if (!session) {
      return;
    }
    setBusy('finish');
    setError(null);
    try {
      const finished = await finishRecording(session.id, {
        create_workflow: false,
      });
      const next =
        finished.draft || finished.status !== 'recording'
          ? finished
          : await getRecording(session.id);
      setSession(next);
      if (!next.draft) {
        setError('录制没有生成可用草稿，请检查是否采集到操作。');
      }
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '结束录制失败。'),
      );
    } finally {
      setBusy(null);
    }
  }

  async function handlePick() {
    if (!session) {
      return;
    }
    setBusy('pick');
    setError(null);
    try {
      const picked = await pickRecordingElement(session.id);
      setSession(picked);
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '拾取控件失败。'),
      );
    } finally {
      setBusy(null);
    }
  }

  async function handleDiscard() {
    if (!session) {
      return;
    }
    setBusy('discard');
    setError(null);
    try {
      await discardRecording(session.id);
      setSession(null);
      onNotice?.({
        tone: 'success',
        message: '录制会话已丢弃。',
      });
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '丢弃录制失败。'),
      );
    } finally {
      setBusy(null);
    }
  }

  function handleApplyDraft() {
    const draftPlan = session?.draft?.plan;
    if (!draftPlan) {
      return;
    }
    onApplyDraft(mergeRecordingDraft(plan, draftPlan));
    onNotice?.({
      tone: 'success',
      message: `已导入 ${draftPlan.steps?.length ?? 0} 个录制步骤。`,
    });
    onClose();
  }

  async function handleCopyCommand() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError('复制录制命令失败。');
    }
  }

  const draft = session?.draft ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">
            录制流程
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            浏览器录制与桌面拾取
          </p>
        </div>
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          onClick={onClose}
          title="关闭录制面板"
          aria-label="关闭录制面板"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <div className="grid grid-cols-2 rounded-md bg-slate-100 p-1">
          {(['web', 'windows'] as RecordingMode[]).map((value) => (
            <button
              key={value}
              type="button"
              disabled={Boolean(isRecording) || busy !== null}
              className={cn(
                'h-8 rounded text-xs font-medium transition-colors disabled:opacity-50',
                mode === value
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800',
              )}
              onClick={() => setMode(value)}
            >
              {value === 'web' ? '网页录制' : '桌面录制'}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            leftIcon={
              busy === 'start' ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Video className="h-3.5 w-3.5" />
              )
            }
            disabled={busy !== null || Boolean(isRecording)}
            onClick={() => {
              void handleStart();
            }}
          >
            开始录制
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
            disabled={!session || busy !== null}
            onClick={() => {
              void handleRefresh();
            }}
          >
            刷新
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Square className="h-3.5 w-3.5" />}
            disabled={!session || !isRecording || busy !== null}
            onClick={() => {
              void handleFinish();
            }}
          >
            结束录制
          </Button>
        </div>

        {session ? (
          <div className="space-y-3 rounded-md border border-slate-200 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-slate-900">
                  {session.name || '未命名录制'}
                </p>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  {session.id.slice(0, 8)} · {session.event_count} 个事件
                  {draft ? ` · ${draft.steps.length} 个步骤` : ''}
                  {draft?.dropped_count
                    ? ` · 丢弃 ${draft.dropped_count}`
                    : ''}
                </p>
              </div>
              <span
                className={cn(
                  'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold',
                  isRecording
                    ? 'bg-red-100 text-red-700'
                    : 'bg-emerald-100 text-emerald-700',
                )}
              >
                {isRecording ? 'REC' : '结束'}
              </span>
            </div>

            {isRecording && session.mode === 'windows' ? (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={
                  busy === 'pick' ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Crosshair className="h-3.5 w-3.5" />
                  )
                }
                disabled={busy !== null}
                onClick={() => {
                  void handlePick();
                }}
              >
                拾取控件
              </Button>
            ) : null}

            {isRecording && session.mode === 'web' ? (
              <div className="rounded-md bg-slate-950 p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
                    recorder
                  </p>
                  <button
                    type="button"
                    className="flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-white"
                    onClick={() => {
                      void handleCopyCommand();
                    }}
                    title={copied ? '已复制' : '复制录制命令'}
                    aria-label={
                      copied ? '已复制' : '复制录制命令'
                    }
                  >
                    {copied ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Clipboard className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
                <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-slate-200">
                  {command}
                </pre>
              </div>
            ) : null}

            {draft?.warnings?.length ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-800">
                  <TriangleAlert className="h-3.5 w-3.5" />
                  警告 {draft.warnings.length}
                </div>
                <ul className="mt-1.5 space-y-1">
                  {draft.warnings.map((warning, index) => (
                    <li
                      key={`${warning}-${index}`}
                      className="break-all text-[11px] leading-4 text-amber-800"
                    >
                      {warning}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {draft?.steps?.length ? (
              <div className="space-y-1.5">
                {draft.steps.map((step, index) => (
                  <div
                    key={`${String(step.step_id)}-${index}`}
                    className="rounded border border-slate-100 bg-slate-50 px-2.5 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="min-w-0 truncate text-xs font-medium text-slate-800">
                        {index + 1}. {String(step.action ?? '')}
                      </p>
                      <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
                        {String(
                          step.executor ??
                            draft.plan.executor_type ??
                            session.mode,
                        )}
                      </span>
                    </div>
                    <p className="mt-1 break-all text-[11px] leading-4 text-slate-500">
                      {describeRecordingStep(step)}
                    </p>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={!draft || busy !== null}
                onClick={handleApplyDraft}
              >
                应用到编辑器
              </Button>
              <Button
                variant="ghost"
                size="sm"
                leftIcon={
                  busy === 'discard' ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )
                }
                disabled={busy !== null}
                onClick={() => {
                  void handleDiscard();
                }}
              >
                丢弃
              </Button>
            </div>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-6 text-center">
            <Video className="mx-auto h-5 w-5 text-slate-400" />
            <p className="mt-2 text-xs font-medium text-slate-600">
              还没有进行中的录制
            </p>
          </div>
        )}

        {session?.error ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5 text-[11px] text-amber-800">
            {session.error}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-2.5 text-[11px] text-red-700">
            {error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default WorkflowRecordingPanel;

import { useEffect, useMemo, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  AlertTriangle,
  LoaderCircle,
  Play,
  Sparkles,
  X,
} from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';

import { getActionCatalog } from '../api/actionCatalog';
import { dryRunWorkflow, testWorkflowStep } from '../api/dryRun';
import {
  draftFromGoal,
  observeWorkflow,
} from '../api/workflowAuthoring';
import {
  getWorkflow,
  publishWorkflow,
  updateWorkflow,
} from '../api/workflows';
import { listWorkflowVersions } from '../api/versions';
import { queryKeys } from '../api/queryKeys';
import { ActionInspector } from '../components/studio/ActionInspector';
import { RunDrawer } from '../components/studio/RunDrawer';
import { StepOutline } from '../components/studio/StepOutline';
import { StepSequenceView } from '../components/studio/StepSequenceView';
import { StudioLayout } from '../components/studio/StudioLayout';
import { StudioTopBar } from '../components/studio/StudioTopBar';
import { VariableList } from '../components/studio/VariableList';
import { WorkflowVersionDiff } from '../components/studio/WorkflowVersionDiff';
import { WorkflowDagPreview } from '../components/WorkflowDagPreview';
import { WorkflowRecordingPanel } from '../components/workflows/WorkflowRecordingPanel';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import {
  collectVariableHints,
} from '../lib/action-catalog';
import { getApiErrorMessage } from '../lib/errors';
import {
  ensureExplicitDependencies,
} from '../lib/workflow-editor';
import { useWorkflowStudioStore } from '../store/useWorkflowStudioStore';
import type {
  StructuredPlan,
  WorkflowDraftExecutionResult,
  WorkflowDraftResult,
  WorkflowRecord,
  WorkflowStep,
} from '../types/workflow';

export function WorkflowEditor() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams();
  const workflowId = Number(id);
  const [showGraph, setShowGraph] = useState(false);
  const [showDraftDialog, setShowDraftDialog] = useState(false);
  const [showRecording, setShowRecording] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [draftGoal, setDraftGoal] = useState('');
  const [startUrl, setStartUrl] = useState('');
  const [draftResult, setDraftResult] = useState<WorkflowDraftResult | null>(null);
  const [observation, setObservation] =
    useState<Record<string, unknown> | null>(null);
  const [isDrafting, setIsDrafting] = useState(false);
  const [runResult, setRunResult] =
    useState<WorkflowDraftExecutionResult | null>(null);
  const [notice, setNotice] = useState<{
    tone: 'success' | 'error' | 'warning';
    message: string;
  } | null>(null);

  const workflowQuery = useQuery({
    queryKey: queryKeys.workflow(workflowId),
    queryFn: () => getWorkflow(workflowId),
    enabled: Number.isFinite(workflowId),
  });
  const catalogQuery = useQuery({
    queryKey: queryKeys.actionCatalog,
    queryFn: getActionCatalog,
  });
  const versionsQuery = useQuery({
    queryKey: queryKeys.workflowVersions(workflowId),
    queryFn: () => listWorkflowVersions(workflowId),
    enabled: Number.isFinite(workflowId),
  });
  const store = useWorkflowStudioStore();
  const saveMutation = useMutation({
    mutationFn: async ({
      target,
      plan,
      revision,
    }: {
      target: WorkflowRecord;
      plan: StructuredPlan;
      revision: number;
    }) => updateWorkflow(target.id, {
      structured_plan: plan,
      generated_code: null,
      executor_type: plan.executor_type,
      draft_revision: revision,
    }),
  });
  const publishMutation = useMutation({
    mutationFn: (targetWorkflowId: number) => publishWorkflow(targetWorkflowId),
  });
  const runMutation = useMutation({
    mutationFn: async ({
      targetWorkflowId,
      plan,
      revision,
      stepId,
    }: {
      targetWorkflowId: number;
      plan: StructuredPlan;
      revision: number;
      stepId?: number;
    }) => (
      stepId
        ? testWorkflowStep(targetWorkflowId, {
            step_id: stepId,
            plan,
            variables: {},
            draft_revision: revision,
          })
        : dryRunWorkflow(targetWorkflowId, {
            plan,
            variables: {},
            draft_revision: revision,
          })
    ),
  });
  const isSaving = saveMutation.isPending;
  const isPublishing = publishMutation.isPending;
  const isRunning = runMutation.isPending;
  const workflow = workflowQuery.data ?? null;
  const steps = (store.draft.steps ?? []) as WorkflowStep[];
  const selectedStep = steps.find(
    (step) => Number(step.step_id) === store.selectedStepId,
  ) ?? null;

  useEffect(() => {
    if (!workflow) {
      return;
    }
    if (
      store.workflowId !== workflow.id
      || (!store.dirty && store.baseRevision !== workflow.draft_revision)
    ) {
      store.load(workflow, null);
    }
  }, [
    store,
    workflow,
  ]);

  useEffect(() => {
    if (!workflow) {
      return;
    }
    const firstUrl = steps
      .map((step) => step.params?.url)
      .find((value) => typeof value === 'string' && /^https?:/i.test(value));
    if (typeof firstUrl === 'string') {
      setStartUrl((current) => current || firstUrl);
    }
  }, [steps, workflow]);

  const variables = useMemo(
    () => collectVariableHints(store.draft).map((hint) => ({
      name: hint.name,
      expression: hint.expression,
    })),
    [store.draft],
  );
  const actions = catalogQuery.data?.actions ?? [];
  const readOnly = workflow?.execution_mode === 'legacy_code';

  async function handleSave(): Promise<boolean> {
    if (!workflow || readOnly) {
      return false;
    }
    setNotice(null);
    try {
      const plan = ensureExplicitDependencies(store.draft);
      const saved = await saveMutation.mutateAsync({
        target: workflow,
        plan,
        revision: store.baseRevision,
      });
      store.markSaved(
        saved.draft_revision,
        ensureExplicitDependencies(saved.structured_plan ?? plan),
      );
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflow(workflow.id),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      setNotice({ tone: 'success', message: '草稿已保存' });
      return true;
    } catch (error) {
      store.markSaveFailed();
      setNotice({
        tone: 'error',
        message: getApiErrorMessage(error, '保存草稿失败'),
      });
      return false;
    }
  }

  async function handlePublish() {
    if (!workflow || readOnly) {
      return;
    }
    setNotice(null);
    try {
      const published = await publishMutation.mutateAsync(workflow.id);
      if (published.status !== 'published') {
        throw new Error('发布未完成');
      }
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflow(workflow.id),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflowVersions(workflow.id),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      setNotice({ tone: 'success', message: '新版本已发布' });
    } catch (error) {
      setNotice({
        tone: 'error',
        message: getApiErrorMessage(error, '发布失败，草稿已保留'),
      });
    }
  }

  async function handleRun(stepOnly = false) {
    if (!workflow) {
      return;
    }
    setNotice(null);
    try {
      const result = await runMutation.mutateAsync({
        targetWorkflowId: workflow.id,
        plan: ensureExplicitDependencies(store.draft),
        revision: store.baseRevision,
        stepId: stepOnly && selectedStep
          ? Number(selectedStep.step_id)
          : undefined,
      });
      setRunResult(result);
      setNotice({
        tone: result.success ? 'success' : 'error',
        message: result.success ? '试运行完成' : result.error ?? '试运行失败',
      });
    } catch (error) {
      setNotice({
        tone: 'error',
        message: getApiErrorMessage(error, '试运行失败'),
      });
    }
  }

  async function handleDraftFromGoal() {
    if (!workflow || !draftGoal.trim()) {
      return;
    }
    setIsDrafting(true);
    setDraftResult(null);
    try {
      const result = await draftFromGoal({
        goal: draftGoal.trim(),
        workflow_id: workflow.id,
        base_plan: store.draft,
        selected_step_id: store.selectedStepId,
        start_url: startUrl || null,
      });
      setDraftResult(result);
      setNotice(
        result.valid
          ? { tone: 'success', message: '草稿差异已生成' }
          : {
              tone: 'warning',
              message: result.errors.join('；') || '草稿未通过校验',
            },
      );
    } catch (error) {
      setNotice({
        tone: 'error',
        message: getApiErrorMessage(error, '生成草稿失败，原草稿已保留'),
      });
    } finally {
      setIsDrafting(false);
    }
  }

  async function handleObserve() {
    if (!workflow || !startUrl.trim()) {
      setNotice({ tone: 'warning', message: '请先填写起始网址' });
      return;
    }
    try {
      const result = await observeWorkflow(workflow.id, {
        url: startUrl.trim(),
        max_elements: 80,
      });
      setObservation(result.observation);
      setNotice({ tone: 'success', message: '页面观察已更新' });
    } catch (error) {
      setNotice({
        tone: 'error',
        message: getApiErrorMessage(error, '页面观察失败'),
      });
    }
  }

  if (workflowQuery.isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <LoaderCircle className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    );
  }
  if (!workflow) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {getApiErrorMessage(workflowQuery.error, '加载工作流失败')}
      </div>
    );
  }

  return (
    <>
      <StudioLayout
        topBar={(
          <StudioTopBar
            workflow={workflow}
            dirty={store.dirty}
            saving={isSaving}
            publishing={isPublishing}
            running={isRunning}
            canUndo={store.canUndo}
            canRedo={store.canRedo}
            showAdvanced={showGraph}
            onBack={() => navigate('/workflows')}
            onSave={() => void handleSave()}
            onRun={() => void handleRun(false)}
            onPublish={() => void handlePublish()}
            onVersions={() => setShowVersions(true)}
            onDraft={() => setShowDraftDialog(true)}
            onRecording={() => setShowRecording(true)}
            onUndo={store.undo}
            onRedo={store.redo}
            onToggleAdvanced={() => setShowGraph((value) => !value)}
          />
        )}
        outline={(
          <div className="flex h-full flex-col">
            <StepOutline
              steps={steps}
              selectedStepId={store.selectedStepId}
              onSelect={(stepId) => store.selectStep(stepId)}
              onMove={store.moveStep}
              onAdd={() => store.addStep({
                step_id: Math.max(
                  0,
                  ...steps.map((step) => Number(step.step_id ?? 0)),
                ) + 1,
                executor: 'web',
                action: 'goto',
                params: {},
                depends_on: steps.length
                  ? [steps[steps.length - 1].step_id as number]
                  : [],
              })}
            />
            <div className="border-t border-slate-200 p-2 dark:border-slate-800">
              <Button
                variant="secondary"
                size="sm"
                className="w-full"
                leftIcon={<Play className="h-3.5 w-3.5" />}
                disabled={!selectedStep || isRunning}
                onClick={() => void handleRun(true)}
              >
                试运行当前步骤
              </Button>
            </div>
            <VariableList variables={variables} />
          </div>
        )}
        canvas={showGraph ? (
          <WorkflowDagPreview plan={store.draft} />
        ) : (
          <StepSequenceView
            steps={steps}
            selectedStepId={store.selectedStepId}
            onSelect={(stepId) => store.selectStep(stepId)}
          />
        )}
        inspector={readOnly ? (
          <div className="space-y-3 p-4 text-sm text-slate-600 dark:text-slate-300">
            <Badge variant="secondary">只读旧版本</Badge>
            <p>
              该工作流仍可由历史运行路径执行。需要编辑时，请创建结构化草稿并重新发布。
            </p>
          </div>
        ) : (
          <ActionInspector
            step={selectedStep}
            actions={actions}
            variables={variables}
            credentials={[]}
            onChange={(patch) => {
              if (selectedStep) {
                store.patchStep(Number(selectedStep.step_id), patch);
              }
            }}
          />
        )}
        drawer={(
          <RunDrawer
            result={runResult}
            tab={store.bottomTab}
            onTabChange={store.setBottomTab}
          />
        )}
      />

      {notice ? (
        <div className={`fixed bottom-4 right-4 z-50 max-w-md rounded-md border px-4 py-3 text-sm shadow-lg ${
          notice.tone === 'success'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : notice.tone === 'warning'
              ? 'border-amber-200 bg-amber-50 text-amber-800'
              : 'border-red-200 bg-red-50 text-red-700'
        }`}>
          {notice.message}
        </div>
      ) : null}

      {showDraftDialog ? (
        <DraftGoalDialog
          goal={draftGoal}
          startUrl={startUrl}
          result={draftResult}
          observation={observation}
          busy={isDrafting}
          onGoalChange={setDraftGoal}
          onStartUrlChange={setStartUrl}
          onClose={() => setShowDraftDialog(false)}
          onGenerate={() => void handleDraftFromGoal()}
          onObserve={() => void handleObserve()}
          onApply={() => {
            if (draftResult?.valid) {
              store.replaceDraft(draftResult.plan);
              setShowDraftDialog(false);
            }
          }}
        />
      ) : null}

      {showRecording ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-lg bg-white shadow-xl dark:bg-slate-900">
            <WorkflowRecordingPanel
              workflowId={workflow.id}
              plan={store.draft}
              onApplyDraft={(plan) => store.replaceDraft(plan)}
              onNotice={setNotice}
              onClose={() => setShowRecording(false)}
            />
          </div>
        </div>
      ) : null}

      {showVersions ? (
        <WorkflowVersionDiff
          workflowId={workflow.id}
          activeVersionId={workflow.active_version_id}
          versions={versionsQuery.data ?? []}
          onClose={() => setShowVersions(false)}
          onRestored={(version) => {
            setNotice({
              tone: 'success',
              message: `active version 已切换为 v${version.version}`,
            });
          }}
        />
      ) : null}
    </>
  );
}

function DraftGoalDialog({
  goal,
  startUrl,
  result,
  observation,
  busy,
  onGoalChange,
  onStartUrlChange,
  onClose,
  onGenerate,
  onObserve,
  onApply,
}: {
  goal: string;
  startUrl: string;
  result: WorkflowDraftResult | null;
  observation: Record<string, unknown> | null;
  busy: boolean;
  onGoalChange: (value: string) => void;
  onStartUrlChange: (value: string) => void;
  onClose: () => void;
  onGenerate: () => void;
  onObserve: () => void;
  onApply: () => void;
}) {
  const inputClass =
    'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
      <div className="max-h-[88vh] w-full max-w-2xl overflow-auto rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="h-4 w-4 text-brand-600" />
            描述生成草稿
          </h2>
          <Button
            variant="ghost"
            size="sm"
            aria-label="关闭"
            leftIcon={<X className="h-4 w-4" />}
            onClick={onClose}
          />
        </header>
        <div className="space-y-3 p-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium">目标或修改要求</span>
            <textarea
              className="min-h-24 w-full rounded-md border border-slate-200 bg-white p-3 text-sm outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900"
              value={goal}
              onChange={(event) => onGoalChange(event.target.value)}
              placeholder="例如：筛选 amount 大于 1000 的记录并导出"
            />
          </label>
          <div className="flex gap-2">
            <input
              className={inputClass}
              value={startUrl}
              onChange={(event) => onStartUrlChange(event.target.value)}
              placeholder="https://example.com"
            />
            <Button variant="secondary" onClick={onObserve}>
              观察页面
            </Button>
          </div>
          {observation ? (
            <div className="rounded-md border border-slate-200 p-3 text-xs text-slate-500 dark:border-slate-800">
              已获取页面观察
              <Badge variant="success" className="ml-2">
                {Array.isArray(observation.elements) ? observation.elements.length : 0} 元素
              </Badge>
            </div>
          ) : null}
          {result ? (
            <div className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <div className="flex items-center gap-2 text-sm">
                <Badge variant={result.valid ? 'success' : 'danger'}>
                  {result.valid ? '校验通过' : '校验失败'}
                </Badge>
                <span className="text-xs text-slate-500">
                  {result.changes.length} 项差异
                </span>
              </div>
              {result.changes.length ? (
                <div className="max-h-52 space-y-1 overflow-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-200">
                  {result.changes.map((change, index) => (
                    <p key={`${change.path}-${index}`}>
                      <span className={change.kind === 'add' ? 'text-emerald-400' : change.kind === 'remove' ? 'text-red-400' : 'text-amber-300'}>
                        {change.kind}
                      </span>{' '}
                      {change.path}: {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                    </p>
                  ))}
                </div>
              ) : null}
              {result.errors.length ? (
                <div className="text-xs text-red-600">
                  {result.errors.map((error) => <p key={error}>{error}</p>)}
                </div>
              ) : null}
              {!result.valid ? (
                <p className="flex items-center gap-1.5 text-xs text-amber-700">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  原草稿保持不变
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
        <footer className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button
            variant="secondary"
            onClick={onGenerate}
            disabled={busy || !goal.trim()}
          >
            生成差异
          </Button>
          <Button onClick={onApply} disabled={!result?.valid}>
            应用到草稿
          </Button>
        </footer>
      </div>
    </div>
  );
}

export default WorkflowEditor;

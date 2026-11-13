import { AlertTriangle, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { getActionCatalog } from '../../api/actionCatalog';
import {
  actionOptionsForExecutor,
  collectVariableHints,
  defaultActionForExecutor,
  executorUsesCatalogForm,
  schemaToActionFields,
  type ActionCatalog,
} from '../../lib/action-catalog';
import {
  buildMCPArguments,
  buildMCPStep,
  parseMCPStep,
  schemaToFields,
} from '../../lib/mcp-workflow';
import { riskLabel, riskVariant } from '../../lib/mcp-settings';
import {
  hostRequirementOptionsForExecutor,
  normalizeHostRequirement,
} from '../../lib/hosts';
import { cn } from '../../lib/utils';
import type { MCPServerRecord, MCPToolRecord } from '../../types/mcp';
import type {
  StepExecutorType,
  StructuredPlan,
  WorkflowStep,
} from '../../types/workflow';
import { Badge } from '../ui/Badge';
import { Switch } from '../ui/Switch';
import { ActionCatalogForm } from './ActionCatalogForm';
import { MCPSchemaForm } from './MCPSchemaForm';

export type WorkflowSelection =
  | { kind: 'trigger' }
  | { kind: 'step'; index: number };

export interface WorkflowConfigPanelProps {
  plan: StructuredPlan;
  selection: WorkflowSelection;
  mcpServers?: MCPServerRecord[];
  onChange: (plan: StructuredPlan) => void;
  onClose: () => void;
}

const ACTION_OPTIONS = [
  ['navigate', '打开页面'],
  ['click', '点击元素'],
  ['fill', '填写输入'],
  ['extract', '提取数据'],
  ['compare', '数据对比'],
  ['notify', '发送通知'],
  ['wait', '等待条件'],
  ['press', '键盘操作'],
  ['screenshot', '页面截图'],
];

const EXECUTOR_OPTIONS: Array<{
  value: StepExecutorType;
  label: string;
}> = [
  { value: 'web', label: '网页' },
  { value: 'transform', label: '数据' },
  { value: 'files', label: '文件' },
  { value: 'email', label: '邮件' },
  { value: 'windows', label: '桌面' },
  { value: 'mcp', label: 'MCP' },
  { value: 'code', label: '代码' },
];

const inputClass =
  'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

const textareaClass =
  'min-h-[150px] w-full resize-y rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-100 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20';

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
        {label}
      </span>
      {children}
    </div>
  );
}

function parameterField(action: string): {
  key: string;
  label: string;
  placeholder: string;
} | null {
  if (action === 'navigate' || action === 'goto') {
    return {
      key: 'url',
      label: '目标网址',
      placeholder: 'https://example.com',
    };
  }
  if (action === 'fill') {
    return {
      key: 'value',
      label: '填写内容',
      placeholder: '输入值或变量',
    };
  }
  if (action === 'press') {
    return {
      key: 'key',
      label: '按键',
      placeholder: 'Enter',
    };
  }
  if (action === 'wait') {
    return {
      key: 'timeout',
      label: '超时时间（毫秒）',
      placeholder: '30000',
    };
  }
  if (action === 'notify') {
    return {
      key: 'message',
      label: '通知内容',
      placeholder: '流程执行完成',
    };
  }
  return null;
}

function availableTools(server: MCPServerRecord | undefined): MCPToolRecord[] {
  return (server?.tools ?? []).filter((tool) => tool.available !== false);
}

export function WorkflowConfigPanel({
  plan,
  selection,
  mcpServers = [],
  onChange,
  onClose,
}: WorkflowConfigPanelProps) {
  const steps = useMemo(
    () => (Array.isArray(plan.steps) ? plan.steps : []),
    [plan.steps],
  );
  const selectedStep =
    selection.kind === 'step' ? steps[selection.index] : undefined;
  const selectionKey = selection.kind === 'trigger'
    ? 'trigger'
    : `step-${selection.index}-${String(selectedStep?.step_id ?? '')}`;
  const [paramsText, setParamsText] = useState(
    () => JSON.stringify(selectedStep?.params ?? {}, null, 2),
  );
  const [paramsError, setParamsError] = useState<string | null>(null);
  const [actionCatalog, setActionCatalog] = useState<ActionCatalog | null>(
    null,
  );
  const [showVariables, setShowVariables] = useState(false);

  useEffect(() => {
    setParamsText(JSON.stringify(selectedStep?.params ?? {}, null, 2));
    setParamsError(null);
    setShowVariables(false);
  }, [selectionKey]);

  useEffect(() => {
    let active = true;
    void getActionCatalog()
      .then((catalog) => {
        if (active) {
          setActionCatalog(catalog);
        }
      })
      .catch(() => {
        if (active) {
          setActionCatalog(null);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  function updateStep(index: number, patch: Partial<WorkflowStep>) {
    const nextSteps = steps.map((step, stepIndex) => (
      stepIndex === index ? { ...step, ...patch } : step
    ));
    onChange({ ...plan, steps: nextSteps });
  }

  function updateTrigger(patch: Record<string, unknown>) {
    onChange({
      ...plan,
      trigger: {
        ...(plan.trigger ?? {}),
        ...patch,
      },
    });
  }

  function handleParamsChange(value: string) {
    setParamsText(value);
    try {
      const parsed = JSON.parse(value) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setParamsError('参数必须是 JSON 对象');
        return;
      }
      setParamsError(null);
      if (selection.kind === 'step') {
        updateStep(selection.index, {
          params: parsed as Record<string, unknown>,
        });
      }
    } catch {
      setParamsError('JSON 格式不完整');
    }
  }

  if (selection.kind === 'trigger') {
    const triggerType =
      typeof plan.trigger?.type === 'string'
        ? plan.trigger.type
        : 'manual';
    const cron =
      typeof plan.trigger?.cron === 'string'
        ? plan.trigger.cron
        : '';

    return (
      <div className="flex h-full min-h-0 flex-col">
        <PanelHeader title="触发节点" subtitle="流程启动条件" onClose={onClose} />
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          <Field label="触发方式">
            <select
              className={inputClass}
              value={triggerType}
              onChange={(event) => {
                const nextType = event.target.value;
                updateTrigger({
                  type: nextType,
                  cron: nextType === 'cron' ? cron : null,
                });
              }}
            >
              <option value="manual">手动触发</option>
              <option value="cron">定时触发</option>
            </select>
          </Field>
          {triggerType === 'cron' ? (
            <Field label="Cron 表达式">
              <input
                className={inputClass}
                value={cron}
                placeholder="0 9 * * *"
                onChange={(event) => updateTrigger({ cron: event.target.value })}
              />
            </Field>
          ) : null}
          <JsonPreview label="实时 JSON" value={plan.trigger ?? {}} />
        </div>
      </div>
    );
  }

  if (selection.kind !== 'step' || !selectedStep) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-slate-500">
        未找到步骤配置。
      </div>
    );
  }

  const step = selectedStep;
  const stepIndex = selection.index;
  const executor = (step.executor ?? 'web') as StepExecutorType;
  const action = String(step.action ?? '');
  const commonParameter = executor === 'web' ? null : parameterField(action);
  const commonValue = commonParameter
    ? step.params?.[commonParameter.key]
    : null;
  const mcpConfig = parseMCPStep(step);
  const hostRequirement = normalizeHostRequirement(
    step.host_requirement,
  );
  const hostRequirementOptions = hostRequirementOptionsForExecutor(
    executor,
  );
  const dataActions = actionOptionsForExecutor(actionCatalog, 'transform');
  const fileActions = actionOptionsForExecutor(actionCatalog, 'files');
  const emailActions = actionOptionsForExecutor(actionCatalog, 'email');
  const webActions = actionOptionsForExecutor(actionCatalog, 'web');
  const windowsActions = actionOptionsForExecutor(actionCatalog, 'windows');
  const genericActions = executor === 'files'
    ? fileActions
    : executor === 'transform'
      ? dataActions
      : executor === 'email'
        ? emailActions
      : executor === 'windows'
        ? windowsActions
        : webActions;
  const genericSchema =
    genericActions.find((item) => item.id === action)?.params_schema
    ?? genericActions[0]?.params_schema
    ?? {};
  const genericFields = schemaToActionFields(genericSchema);
  const variableHints = collectVariableHints(plan);
  const selectedServer = mcpServers.find(
    (server) => server.name === mcpConfig.server,
  );
  const tools = availableTools(selectedServer);
  const selectedTool = tools.find((tool) => tool.name === mcpConfig.tool)
    ?? tools[0];
  const schemaFields = schemaToFields(selectedTool?.input_schema);

  function updateMCPConfig(patch: {
    server?: string;
    tool?: string;
    arguments?: Record<string, unknown>;
    resultVar?: string;
    resultPath?: string;
    confirm?: boolean;
    timeoutSeconds?: number | null;
    retryAttempts?: number;
  }) {
    const nextServerName = patch.server ?? mcpConfig.server;
    const nextServer = mcpServers.find(
      (server) => server.name === nextServerName,
    );
    const nextTools = availableTools(nextServer);
    const nextToolName = patch.tool ?? (
      nextTools.some((tool) => tool.name === mcpConfig.tool)
        ? mcpConfig.tool
        : nextTools[0]?.name ?? ''
    );
    const nextTool = nextTools.find((tool) => tool.name === nextToolName);
    const serverChanged = patch.server !== undefined;
    const toolChanged = patch.tool !== undefined || serverChanged;
    const nextArguments = patch.arguments ?? (
      toolChanged
        ? buildMCPArguments(schemaToFields(nextTool?.input_schema), {})
        : mcpConfig.arguments
    );
    const nextStep = buildMCPStep({
      stepId: step.step_id,
      server: nextServerName,
      tool: nextToolName,
      arguments: nextArguments,
      resultVar: patch.resultVar ?? mcpConfig.resultVar,
      resultPath: patch.resultPath ?? mcpConfig.resultPath,
      confirm: patch.confirm ?? mcpConfig.confirm,
      timeoutSeconds:
        patch.timeoutSeconds === undefined
          ? mcpConfig.timeoutSeconds
          : patch.timeoutSeconds,
      retryAttempts: patch.retryAttempts ?? mcpConfig.retryAttempts,
      dependsOn: step.depends_on ?? [],
    });
    updateStep(stepIndex, {
      ...nextStep,
      condition: step.condition,
      enabled: step.enabled,
      idempotency_key: step.idempotency_key,
      on_failure: step.on_failure,
    });
    setParamsText(JSON.stringify(nextStep.params ?? {}, null, 2));
    setParamsError(null);
  }

  function handleExecutorChange(nextExecutor: StepExecutorType) {
    if (nextExecutor === 'mcp') {
      const firstServer = mcpServers[0];
      const firstTool = availableTools(firstServer)[0];
      const nextStep = buildMCPStep({
        stepId: step.step_id,
        server: firstServer?.name ?? '',
        tool: firstTool?.name ?? '',
        arguments: buildMCPArguments(
          schemaToFields(firstTool?.input_schema),
          {},
        ),
        confirm: false,
        timeoutSeconds: 30,
        retryAttempts: 2,
        dependsOn: step.depends_on ?? [],
      });
      updateStep(stepIndex, {
        ...nextStep,
        target: undefined,
        selector: undefined,
      });
      setParamsText(JSON.stringify(nextStep.params ?? {}, null, 2));
      return;
    }
    const nextAction = nextExecutor === 'windows'
      ? 'launch_app'
      : nextExecutor === 'code'
        ? 'run_code'
        : nextExecutor === 'transform'
          ? defaultActionForExecutor(actionCatalog, 'transform') || 'filter'
          : nextExecutor === 'files'
            ? defaultActionForExecutor(actionCatalog, 'files') || 'write_csv'
            : nextExecutor === 'email'
              ? defaultActionForExecutor(actionCatalog, 'email') || 'send'
          : 'navigate';
    updateStep(stepIndex, {
      executor: nextExecutor,
      action: nextAction,
      params: {},
      host_requirement: 'any',
    });
    setParamsText('{}');
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PanelHeader
        title={`步骤 ${stepIndex + 1}`}
        subtitle={String(step.step_id ?? stepIndex + 1)}
        onClose={onClose}
      />
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <Field label="执行器">
          <select
            className={inputClass}
            value={executor}
            onChange={(event) =>
              handleExecutorChange(event.target.value as StepExecutorType)
            }
          >
            {EXECUTOR_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="执行主机要求">
          <select
            className={inputClass}
            value={hostRequirement}
            onChange={(event) =>
              updateStep(stepIndex, {
                host_requirement: normalizeHostRequirement(
                  event.target.value,
                ),
              })
            }
          >
            {hostRequirementOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>

        {executor === 'mcp' ? (
          <>
            {mcpServers.length === 0 ? (
              <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>当前没有可用应用。请先在“应用”页面添加并测试连接。</span>
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="应用">
                <select
                  className={inputClass}
                  value={mcpConfig.server}
                  onChange={(event) =>
                    updateMCPConfig({ server: event.target.value })
                  }
                >
                  <option value="">请选择应用</option>
                  {mcpServers.map((server) => (
                    <option key={server.id} value={server.name}>
                      {server.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="工具">
                <select
                  className={inputClass}
                  value={selectedTool?.name ?? mcpConfig.tool}
                  onChange={(event) =>
                    updateMCPConfig({ tool: event.target.value })
                  }
                  disabled={!selectedServer}
                >
                  <option value="">请选择工具</option>
                  {tools.map((tool) => (
                    <option key={tool.name} value={tool.name}>
                      {tool.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {selectedTool ? (
              <div className="space-y-1 rounded-md border border-slate-200 px-3 py-2.5 dark:border-slate-800">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-all text-xs font-medium text-slate-700 dark:text-slate-200">
                    {selectedTool.name}
                  </span>
                  <Badge variant={riskVariant(selectedTool.risk_level)}>
                    {riskLabel(selectedTool.risk_level)}
                  </Badge>
                </div>
                <p className="break-words text-xs leading-5 text-slate-500">
                  {selectedTool.description || '暂无工具说明'}
                </p>
              </div>
            ) : null}

            <Field label="工具参数">
              <MCPSchemaForm
                fields={schemaFields}
                values={mcpConfig.arguments}
                onChange={(nextArguments) =>
                  updateMCPConfig({ arguments: nextArguments })
                }
              />
            </Field>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="输出变量">
                <input
                  className={inputClass}
                  value={mcpConfig.resultVar}
                  placeholder="result"
                  onChange={(event) =>
                    updateMCPConfig({ resultVar: event.target.value })
                  }
                />
              </Field>
              <Field label="结果路径">
                <input
                  className={inputClass}
                  value={mcpConfig.resultPath}
                  placeholder="/artifacts/0/path"
                  onChange={(event) =>
                    updateMCPConfig({ resultPath: event.target.value })
                  }
                />
              </Field>
              <Field label="超时时间（秒）">
                <input
                  type="number"
                  min={1}
                  className={inputClass}
                  value={mcpConfig.timeoutSeconds ?? 30}
                  onChange={(event) =>
                    updateMCPConfig({
                      timeoutSeconds: Number(event.target.value) || null,
                    })
                  }
                />
              </Field>
              <Field label="失败重试次数">
                <input
                  type="number"
                  min={1}
                  max={5}
                  className={inputClass}
                  value={mcpConfig.retryAttempts}
                  onChange={(event) =>
                    updateMCPConfig({
                      retryAttempts: Math.max(
                        1,
                        Math.min(5, Number(event.target.value) || 1),
                      ),
                    })
                  }
                />
              </Field>
            </div>

            <div className="flex items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2.5 dark:border-slate-800">
              <div>
                <p className="text-xs font-medium text-slate-700 dark:text-slate-200">
                  执行前确认
                </p>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  写入或高风险工具建议保持开启。
                </p>
              </div>
              <Switch
                checked={mcpConfig.confirm}
                onCheckedChange={(confirm) =>
                  updateMCPConfig({ confirm })
                }
                label="MCP 执行前确认"
              />
            </div>
          </>
        ) : (
          <>
            <Field label="动作类型">
              <select
                className={inputClass}
                value={action}
                onChange={(event) => {
                  updateStep(stepIndex, {
                    action: event.target.value,
                  });
                }}
              >
                {executorUsesCatalogForm(executor)
                && genericActions.length > 0
                  ? genericActions.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title || item.id}
                      </option>
                    ))
                  : ACTION_OPTIONS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                {!executorUsesCatalogForm(executor)
                && !ACTION_OPTIONS.some(([value]) => value === action) ? (
                  <option value={action}>{action || '自定义'}</option>
                ) : null}
              </select>
            </Field>
            {executorUsesCatalogForm(executor) ? (
              <>
                <Field label="数据参数">
                  <ActionCatalogForm
                    fields={genericFields}
                    values={step.params ?? {}}
                    onChange={(nextParams) => {
                      updateStep(stepIndex, { params: nextParams });
                      setParamsText(JSON.stringify(nextParams, null, 2));
                    }}
                    onVariableRequest={() =>
                      setShowVariables((current) => !current)
                    }
                  />
                </Field>
                {showVariables ? (
                  <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
                    <p className="mb-2 text-xs font-medium text-slate-600 dark:text-slate-300">
                      可用变量
                    </p>
                    {variableHints.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {variableHints.map((hint) => (
                          <button
                            key={hint.name}
                            type="button"
                            className="rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200"
                            title={`复制 ${hint.expression}`}
                            onClick={() => {
                              void navigator.clipboard?.writeText(
                                hint.expression,
                              );
                            }}
                          >
                            {hint.expression}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">
                        前序结果变量会在这里显示。
                      </p>
                    )}
                  </div>
                ) : null}
              </>
            ) : null}
            <Field label="目标">
              <input
                className={inputClass}
                value={String(step.target ?? '')}
                placeholder="目标描述"
                onChange={(event) =>
                  updateStep(stepIndex, {
                    target: event.target.value,
                  })
                }
              />
            </Field>
            <Field label="选择器">
              <input
                className={inputClass}
                value={String(step.selector ?? '')}
                placeholder="#selector 或 text=登录"
                onChange={(event) =>
                  updateStep(stepIndex, {
                    selector: event.target.value,
                  })
                }
              />
            </Field>
            {!executorUsesCatalogForm(executor)
            && commonParameter ? (
              <Field label={commonParameter.label}>
                <input
                  className={inputClass}
                  value={
                    commonValue === null || commonValue === undefined
                      ? ''
                      : String(commonValue)
                  }
                  placeholder={commonParameter.placeholder}
                  onChange={(event) => {
                    const nextParams = {
                      ...(step.params ?? {}),
                    };
                    if (event.target.value) {
                      nextParams[commonParameter.key] = event.target.value;
                    } else {
                      delete nextParams[commonParameter.key];
                    }
                    updateStep(stepIndex, { params: nextParams });
                    setParamsText(JSON.stringify(nextParams, null, 2));
                  }}
                />
              </Field>
            ) : null}
          </>
        )}

        <Field
          label={
            executor === 'mcp'
              ? '参数 JSON（完整 MCP 参数）'
              : '参数 JSON'
          }
        >
          <textarea
            className={cn(
              textareaClass,
              paramsError && 'border-red-500 ring-2 ring-red-500/20',
            )}
            value={paramsText}
            spellCheck={false}
            onChange={(event) => handleParamsChange(event.target.value)}
          />
        </Field>
        {paramsError ? (
          <p className="text-xs text-red-600">{paramsError}</p>
        ) : null}

        {step.depends_on?.length ? (
          <Field label="前置依赖">
            <div className="flex flex-wrap gap-1.5">
              {step.depends_on.map((dependency, index) => (
                <span
                  key={`${String(dependency)}-${index}`}
                  className="rounded bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                >
                  {String(dependency)}
                </span>
              ))}
            </div>
          </Field>
        ) : null}

        <JsonPreview label="实时 JSON" value={step} />
      </div>
    </div>
  );
}

function PanelHeader({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  return (
    <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
          {title}
        </p>
        <p className="mt-0.5 truncate text-xs text-slate-500">{subtitle}</p>
      </div>
      <button
        type="button"
        className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
        onClick={onClose}
        title="关闭配置"
        aria-label="关闭配置"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

function JsonPreview({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-slate-600 dark:text-slate-300">
        {label}
      </p>
      <pre className="max-h-72 overflow-auto rounded-md bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-100">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

export default WorkflowConfigPanel;

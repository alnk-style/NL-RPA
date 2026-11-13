import { useMemo, useState } from 'react';
import { Braces, Code2, SlidersHorizontal } from 'lucide-react';

import type { ActionDefinition } from '../../lib/action-catalog';
import {
  schemaToStudioFields,
} from '../../lib/action-schema';
import {
  hostRequirementOptionsForExecutor,
  normalizeHostRequirement,
} from '../../lib/hosts';
import { cn } from '../../lib/utils';
import type {
  StepExecutorType,
  WorkflowStep,
} from '../../types/workflow';
import { Button } from '../ui/Button';
import { ActionSchemaForm } from './ActionSchemaForm';
import { ConditionBuilder } from './ConditionBuilder';
import { JsonAdvancedEditor } from './JsonAdvancedEditor';
import type { VariableChoice } from './VariablePicker';

const inputClass =
  'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900';

const FIELD_LABELS: Record<string, string> = {
  title: '步骤名称',
  executor: '执行器',
  action: '动作',
  timeout_seconds: '超时秒数',
  on_failure: '失败策略',
  result_var: '结果变量',
  idempotency_key: '幂等键',
  host_requirement: '执行主机要求',
};

export function ActionInspector({
  step,
  actions,
  variables,
  credentials,
  onChange,
}: {
  step: WorkflowStep | null;
  actions: ActionDefinition[];
  variables: VariableChoice[];
  credentials: string[];
  onChange: (patch: Partial<WorkflowStep>) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const [showCondition, setShowCondition] = useState(false);
  const executors = useMemo(
    () => Array.from(new Set(actions.map((item) => item.executor))),
    [actions],
  );
  if (!step) {
    return (
      <aside className="flex h-full items-center justify-center p-5 text-sm text-slate-500">
        选择一个步骤
      </aside>
    );
  }
  const executor = String(step.executor ?? 'web');
  const availableActions = actions.filter(
    (item) => item.executor === executor,
  );
  const definition = availableActions.find(
    (item) => item.id === step.action,
  ) ?? availableActions[0];
  const fields = schemaToStudioFields(definition?.params_schema);
  const retryPolicy = step.retry_policy ?? {};
  const hostRequirement = normalizeHostRequirement(
    step.host_requirement,
  );
  const hostRequirementOptions = hostRequirementOptionsForExecutor(executor);

  function updateAction(nextAction: string) {
    onChange({ action: nextAction, params: {} });
  }

  function updateExecutor(nextExecutor: string) {
    const first = actions.find((item) => item.executor === nextExecutor);
    onChange({
      executor: nextExecutor as StepExecutorType,
      action: first?.id ?? '',
      params: {},
      host_requirement: 'any',
    });
  }

  return (
    <aside className="flex h-full min-h-0 flex-col bg-white dark:bg-slate-900">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2.5 dark:border-slate-800">
        <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <SlidersHorizontal className="h-3.5 w-3.5" />
          属性
        </h2>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Code2 className="h-3.5 w-3.5" />}
          onClick={() => setAdvanced((value) => !value)}
        >
          JSON
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {advanced ? (
          <JsonAdvancedEditor
            key={String(step.step_id)}
            value={step as Record<string, unknown>}
            onChange={(next) => onChange(next as Partial<WorkflowStep>)}
          />
        ) : (
          <>
            <Field label={FIELD_LABELS.title}>
              <input
                className={inputClass}
                value={String(step.title ?? '')}
                onChange={(event) => onChange({ title: event.target.value })}
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label={FIELD_LABELS.executor}>
                <select
                  className={inputClass}
                  value={executor}
                  onChange={(event) => updateExecutor(event.target.value)}
                >
                  {executors.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </Field>
              <Field label={FIELD_LABELS.action}>
                <select
                  className={inputClass}
                  value={String(step.action ?? definition?.id ?? '')}
                  onChange={(event) => updateAction(event.target.value)}
                >
                  {availableActions.map((item) => (
                    <option key={item.id} value={item.id}>{item.title || item.id}</option>
                  ))}
                </select>
              </Field>
            </div>

            <Field label={FIELD_LABELS.host_requirement}>
              <select
                className={inputClass}
                value={hostRequirement}
                onChange={(event) => onChange({
                  host_requirement: normalizeHostRequirement(
                    event.target.value,
                  ),
                })}
              >
                {hostRequirementOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>

            <section className="space-y-2">
              <h3 className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                动作参数
              </h3>
              <ActionSchemaForm
                fields={fields}
                values={step.params ?? {}}
                variables={variables}
                credentials={credentials}
                onChange={(params) => onChange({ params })}
              />
            </section>

            <div className="grid grid-cols-2 gap-2">
              <Field label={FIELD_LABELS.timeout_seconds}>
                <input
                  type="number"
                  min={1}
                  className={inputClass}
                  value={Number(step.timeout_seconds ?? 60)}
                  onChange={(event) => onChange({
                    timeout_seconds: Number(event.target.value),
                  })}
                />
              </Field>
              <Field label={FIELD_LABELS.on_failure}>
                <select
                  className={inputClass}
                  value={String(step.on_failure ?? 'stop')}
                  onChange={(event) => onChange({
                    on_failure: event.target.value,
                  })}
                >
                  <option value="stop">停止</option>
                  <option value="pause">暂停</option>
                  <option value="skip">跳过</option>
                </select>
              </Field>
              <Field label="最大尝试">
                <input
                  type="number"
                  min={1}
                  max={10}
                  className={inputClass}
                  value={Number(retryPolicy.max_attempts ?? 1)}
                  onChange={(event) => onChange({
                    retry_policy: {
                      ...retryPolicy,
                      max_attempts: Number(event.target.value),
                    },
                  })}
                />
              </Field>
              <Field label="退避秒数">
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  className={inputClass}
                  value={Number(retryPolicy.backoff_seconds ?? 0)}
                  onChange={(event) => onChange({
                    retry_policy: {
                      ...retryPolicy,
                      backoff_seconds: Number(event.target.value),
                    },
                  })}
                />
              </Field>
            </div>

            <Field label={FIELD_LABELS.result_var}>
              <input
                className={inputClass}
                value={String(step.result_var ?? '')}
                placeholder="result"
                onChange={(event) => onChange({
                  result_var: event.target.value || undefined,
                })}
              />
            </Field>
            <Field label={FIELD_LABELS.idempotency_key}>
              <input
                className={inputClass}
                value={String(step.idempotency_key ?? '')}
                onChange={(event) => onChange({
                  idempotency_key: event.target.value || null,
                })}
              />
            </Field>

            <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <button
                type="button"
                className="flex w-full items-center justify-between text-xs font-medium text-slate-600 dark:text-slate-300"
                onClick={() => setShowCondition((value) => !value)}
              >
                <span className="flex items-center gap-1.5">
                  <Braces className="h-3.5 w-3.5" />
                  条件
                </span>
                <span>{step.condition ? '已设置' : '未设置'}</span>
              </button>
              {showCondition ? (
                <div className="mt-3 space-y-2">
                  <ConditionBuilder
                    onChange={(condition) => onChange({ condition })}
                  />
                  {step.condition ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onChange({ condition: undefined })}
                    >
                      清除条件
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn('block space-y-1.5')}>
      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
        {label}
      </span>
      {children}
    </label>
  );
}

export default ActionInspector;

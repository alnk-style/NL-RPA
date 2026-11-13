import {
  ArrowDown,
  ArrowUp,
  GripVertical,
  Plus,
} from 'lucide-react';

import type { WorkflowStep } from '../../types/workflow';
import { Button } from '../ui/Button';
import { cn } from '../../lib/utils';

export function StepOutline({
  steps,
  selectedStepId,
  onSelect,
  onAdd,
  onMove,
}: {
  steps: WorkflowStep[];
  selectedStepId: number | null;
  onSelect: (stepId: number) => void;
  onAdd: () => void;
  onMove: (from: number, to: number) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2.5 dark:border-slate-800">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          步骤
        </h2>
        <Button
          variant="ghost"
          size="sm"
          aria-label="添加步骤"
          title="添加步骤"
          leftIcon={<Plus className="h-3.5 w-3.5" />}
          onClick={onAdd}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {steps.map((step, index) => {
          const stepId = Number(step.step_id ?? index + 1);
          return (
            <div
              key={stepId}
              className={cn(
                'group mb-1 rounded-md border px-2 py-2',
                selectedStepId === stepId
                  ? 'border-brand-300 bg-brand-50 dark:border-brand-700 dark:bg-brand-950/30'
                  : 'border-transparent hover:border-slate-200 hover:bg-slate-50 dark:hover:border-slate-700 dark:hover:bg-slate-800/60',
              )}
            >
              <button
                type="button"
                className="flex w-full items-start gap-2 text-left"
                onClick={() => onSelect(stepId)}
              >
                <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                <span className="min-w-0">
                  <span className="block truncate text-xs font-medium">
                    {index + 1}. {String(step.title || step.action || '未命名步骤')}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-slate-500">
                    {step.executor || 'web'} · {step.action || '未选择动作'}
                  </span>
                </span>
              </button>
              <div className="mt-1 flex justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                <button
                  type="button"
                  className="rounded p-1 text-slate-400 hover:bg-slate-200 disabled:opacity-30"
                  disabled={index === 0}
                  title="上移"
                  onClick={() => onMove(index, index - 1)}
                >
                  <ArrowUp className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-slate-400 hover:bg-slate-200 disabled:opacity-30"
                  disabled={index === steps.length - 1}
                  title="下移"
                  onClick={() => onMove(index, index + 1)}
                >
                  <ArrowDown className="h-3 w-3" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default StepOutline;

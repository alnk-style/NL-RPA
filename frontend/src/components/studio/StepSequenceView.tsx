import { ChevronRight, GripVertical } from 'lucide-react';

import type { WorkflowStep } from '../../types/workflow';

export function StepSequenceView({
  steps,
  selectedStepId,
  onSelect,
}: {
  steps: WorkflowStep[];
  selectedStepId: number | null;
  onSelect: (stepId: number) => void;
}) {
  if (!steps.length) {
    return (
      <div className="flex min-h-[360px] items-center justify-center text-sm text-slate-500">
        暂无步骤
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-2 p-4">
      {steps.map((step, index) => {
        const stepId = Number(step.step_id ?? index + 1);
        return (
          <button
            key={stepId}
            type="button"
            onClick={() => onSelect(stepId)}
            className={`flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left ${
              selectedStepId === stepId
                ? 'border-brand-300 bg-brand-50 dark:border-brand-700 dark:bg-brand-950/30'
                : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-700 dark:bg-slate-900'
            }`}
          >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
              {index + 1}
            </span>
            <GripVertical className="h-4 w-4 text-slate-400" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">
                {String(step.title || step.action || '未命名步骤')}
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">
                {step.executor || 'web'} · {step.action || '未选择动作'}
              </span>
            </span>
            <ChevronRight className="h-4 w-4 text-slate-400" />
          </button>
        );
      })}
    </div>
  );
}

export default StepSequenceView;

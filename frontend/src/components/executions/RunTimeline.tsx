import type { ReactNode } from 'react';

import type { ExecutionStepLog } from '../../types/workflow';

export function RunTimeline({
  steps,
  children,
  empty,
}: {
  steps: ExecutionStepLog[];
  children:
    | ReactNode
    | ((step: ExecutionStepLog, index: number) => ReactNode);
  empty?: ReactNode;
}) {
  if (!steps.length) {
    return (
      <div className="flex min-h-40 items-center justify-center text-sm text-slate-500">
        {empty ?? '暂无步骤日志'}
      </div>
    );
  }
  return (
    <ol className="p-4">
      {typeof children === 'function'
        ? steps.map((step, index) => children(step, index))
        : children}
    </ol>
  );
}

export default RunTimeline;

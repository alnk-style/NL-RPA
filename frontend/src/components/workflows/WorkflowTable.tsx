import type { WorkflowRecord } from '../../types/workflow';
import { WorkflowRow } from './WorkflowRow';

export function WorkflowTable({
  workflows,
  busyAction,
  onDuplicate,
  onDelete,
  onRun,
  onTogglePublished,
}: {
  workflows: WorkflowRecord[];
  busyAction: string | null;
  onDuplicate: (workflow: WorkflowRecord) => void;
  onDelete: (workflow: WorkflowRecord) => void;
  onRun: (workflow: WorkflowRecord) => void;
  onTogglePublished: (workflow: WorkflowRecord) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <table className="w-full min-w-[980px] border-collapse text-left text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-950/50">
          <tr>
            <th className="px-4 py-2.5 font-medium">工作流</th>
            <th className="px-4 py-2.5 font-medium">执行模式</th>
            <th className="px-4 py-2.5 font-medium">状态</th>
            <th className="px-4 py-2.5 font-medium">触发</th>
            <th className="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((workflow) => (
            <WorkflowRow
              key={workflow.id}
              workflow={workflow}
              busyAction={busyAction}
              onDuplicate={onDuplicate}
              onDelete={onDelete}
              onRun={onRun}
              onTogglePublished={onTogglePublished}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default WorkflowTable;

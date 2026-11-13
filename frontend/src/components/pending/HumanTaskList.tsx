import {
  CheckCircle2,
  Clock3,
  Globe2,
  KeyRound,
  ScanLine,
  ShieldCheck,
  XCircle,
} from 'lucide-react';

import {
  formatHumanTaskWaitingTime,
  humanTaskKindLabel,
  humanTaskStatusLabel,
  humanTaskStatusVariant,
} from '../../lib/human-tasks';
import { cn } from '../../lib/utils';
import type { HumanTaskRecord } from '../../types/humanTasks';
import { Badge } from '../ui/Badge';

function TaskIcon({ task }: { task: HumanTaskRecord }) {
  if (task.status === 'resolved') {
    return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  }
  if (task.status === 'cancelled' || task.status === 'expired') {
    return <XCircle className="h-4 w-4 text-slate-400" />;
  }
  if (task.kind === 'captcha_required') {
    return <ScanLine className="h-4 w-4 text-amber-600" />;
  }
  if (task.kind === 'mfa_required') {
    return <ShieldCheck className="h-4 w-4 text-amber-600" />;
  }
  if (task.kind === 'login_required') {
    return <Globe2 className="h-4 w-4 text-amber-600" />;
  }
  return <KeyRound className="h-4 w-4 text-amber-600" />;
}

export function HumanTaskList({
  tasks,
  selectedId,
  onSelect,
}: {
  tasks: HumanTaskRecord[];
  selectedId: number | null;
  onSelect: (task: HumanTaskRecord) => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {tasks.map((task) => (
          <button
            key={task.id}
            type="button"
            className={cn(
              'flex w-full items-start gap-3 px-4 py-3 text-left transition-colors',
              selectedId === task.id
                ? 'bg-amber-50/80 dark:bg-amber-950/20'
                : 'hover:bg-slate-50 dark:hover:bg-slate-800/60',
            )}
            onClick={() => onSelect(task)}
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
              <TaskIcon task={task} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium">
                  {task.workflow_name || `工作流 #${task.execution_id}`}
                </span>
                <Badge variant={humanTaskStatusVariant(task.status)}>
                  {humanTaskStatusLabel(task.status)}
                </Badge>
              </span>
              <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                {humanTaskKindLabel(task.kind)} · 执行 #{task.execution_id}
                {task.workflow_version
                  ? ` · v${task.workflow_version}`
                  : ''}
              </span>
              <span className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                <Clock3 className="h-3.5 w-3.5" />
                等待 {formatHumanTaskWaitingTime(task.waiting_seconds)}
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

export default HumanTaskList;

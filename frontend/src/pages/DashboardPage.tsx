import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  FileText,
  Play,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import { listExecutions } from '../api/executions';
import { queryKeys } from '../api/queryKeys';
import { listWorkflows } from '../api/workflows';
import { Badge } from '../components/ui/Badge';

export function DashboardPage() {
  const workflows = useQuery({
    queryKey: queryKeys.workflows,
    queryFn: listWorkflows,
  });
  const executions = useQuery({
    queryKey: queryKeys.executions,
    queryFn: listExecutions,
  });
  const executionRows = executions.data ?? [];
  const running = executionRows.filter(
    (execution) => execution.status === 'running'
      || execution.status === 'pending',
  );
  const failed = executionRows.filter(
    (execution) => execution.status === 'failed',
  );
  const artifacts = executionRows.flatMap(
    (execution) => execution.artifacts ?? [],
  );

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-950 dark:text-white">
            工作台
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            当前自动化状态与最近结果
          </p>
        </div>
        <Link
          to="/workflows"
          className="inline-flex h-9 items-center gap-2 rounded-md bg-brand-600 px-3 text-sm font-medium text-white hover:bg-brand-700"
        >
          <WorkflowIcon className="h-4 w-4" />
          工作流
        </Link>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="工作流"
          value={workflows.data?.length ?? 0}
          icon={WorkflowIcon}
        />
        <Metric label="运行中" value={running.length} icon={Play} />
        <Metric label="失败" value={failed.length} icon={Activity} />
        <Metric label="最近产物" value={artifacts.length} icon={FileText} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <header className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <h2 className="text-sm font-semibold">正在运行</h2>
          </header>
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {running.length ? running.map((execution) => (
              <Link
                key={execution.id}
                to={`/executions/${execution.id}`}
                className="flex items-center justify-between gap-3 px-4 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/60"
              >
                <span className="truncate">
                  {execution.workflow_name ?? `执行 #${execution.id}`}
                </span>
                <Badge variant="warning">{execution.status}</Badge>
              </Link>
            )) : <Empty text="当前没有运行中的执行" />}
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <header className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <h2 className="text-sm font-semibold">最近失败</h2>
          </header>
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {failed.length ? failed.map((execution) => (
              <Link
                key={execution.id}
                to={`/executions/${execution.id}`}
                className="block px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60"
              >
                <p className="truncate text-sm font-medium">
                  {execution.workflow_name ?? `执行 #${execution.id}`}
                </p>
                <p className="mt-1 line-clamp-2 text-xs text-red-600">
                  {execution.error_message ?? '执行失败'}
                </p>
              </Link>
            )) : <Empty text="最近没有失败记录" />}
          </div>
        </section>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: typeof Activity;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        <Icon className="h-4 w-4 text-slate-400" />
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="px-4 py-8 text-center text-sm text-slate-500">{text}</p>;
}

export default DashboardPage;

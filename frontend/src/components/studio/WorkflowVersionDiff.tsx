import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  GitCompareArrows,
  History,
  LoaderCircle,
  RotateCcw,
  X,
} from 'lucide-react';

import { queryKeys } from '../../api/queryKeys';
import {
  diffWorkflowVersions,
  rollbackWorkflow,
} from '../../api/versions';
import { getApiErrorMessage } from '../../lib/errors';
import {
  formatVersionChangeValue,
  groupVersionChanges,
} from '../../lib/version-diff';
import type { WorkflowVersionRecord } from '../../types/workflow';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

export function WorkflowVersionDiff({
  workflowId,
  activeVersionId,
  versions,
  onClose,
  onRestored,
}: {
  workflowId: number;
  activeVersionId: number | null;
  versions: WorkflowVersionRecord[];
  onClose: () => void;
  onRestored: (version: WorkflowVersionRecord) => void;
}) {
  const queryClient = useQueryClient();
  const ordered = useMemo(
    () => [...versions].sort((left, right) => right.version - left.version),
    [versions],
  );
  const [fromVersionId, setFromVersionId] = useState<number | null>(
    ordered[1]?.id ?? ordered[0]?.id ?? null,
  );
  const [toVersionId, setToVersionId] = useState<number | null>(
    ordered[0]?.id ?? null,
  );
  const [compareRequested, setCompareRequested] = useState(false);
  const [rollbackTarget, setRollbackTarget] =
    useState<WorkflowVersionRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const diffQuery = useQuery({
    queryKey: queryKeys.workflowVersionDiff(
      workflowId,
      fromVersionId ?? 0,
      toVersionId ?? 0,
    ),
    queryFn: () => diffWorkflowVersions(
      workflowId,
      Number(fromVersionId),
      Number(toVersionId),
    ),
    enabled: Boolean(
      compareRequested
      && fromVersionId
      && toVersionId
      && fromVersionId !== toVersionId,
    ),
  });
  const rollbackMutation = useMutation({
    mutationFn: (version: WorkflowVersionRecord) => rollbackWorkflow(
      workflowId,
      version.id,
    ),
  });
  const grouped = groupVersionChanges(diffQuery.data?.changes ?? []);

  async function confirmRollback() {
    if (!rollbackTarget) {
      return;
    }
    setError(null);
    try {
      await rollbackMutation.mutateAsync(rollbackTarget);
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.workflow(workflowId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.workflowVersions(workflowId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.workflows,
        }),
      ]);
      onRestored(rollbackTarget);
      setRollbackTarget(null);
    } catch (rollbackError) {
      setError(getApiErrorMessage(rollbackError, '回滚失败，active version 未改变'));
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <History className="h-4 w-4 text-brand-600" />
          <h2 className="text-sm font-semibold">版本历史</h2>
          <Badge variant="secondary">{versions.length} 个版本</Badge>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            aria-label="关闭版本历史"
            title="关闭"
            leftIcon={<X className="h-4 w-4" />}
            onClick={onClose}
          />
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)]">
          <section className="min-h-0 overflow-auto border-r border-slate-200 p-3 dark:border-slate-800">
            <p className="mb-2 text-xs font-medium text-slate-500">发布版本</p>
            <div className="space-y-1">
              {ordered.map((version) => (
                <div
                  key={version.id}
                  className="rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">v{version.version}</span>
                    {version.id === activeVersionId ? (
                      <Badge variant="success">active</Badge>
                    ) : null}
                    <span className="ml-auto text-slate-400">
                      {new Date(version.created_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                    {version.action_catalog_version}
                  </p>
                  <div className="mt-2 flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={version.id === activeVersionId}
                      onClick={() => setRollbackTarget(version)}
                    >
                      回滚到此版
                    </Button>
                  </div>
                </div>
              ))}
              {!ordered.length ? (
                <p className="py-8 text-center text-sm text-slate-500">
                  尚无已发布版本
                </p>
              ) : null}
            </div>
          </section>

          <section className="min-h-0 overflow-auto p-4">
            <div className="flex flex-wrap items-end gap-2">
              <label className="space-y-1 text-xs">
                <span className="block text-slate-500">基准版本</span>
                <select
                  className="h-8 min-w-28 rounded-md border border-slate-200 bg-white px-2 dark:border-slate-700 dark:bg-slate-900"
                  value={fromVersionId ?? ''}
                  onChange={(event) => {
                    setCompareRequested(false);
                    setFromVersionId(Number(event.target.value) || null);
                  }}
                >
                  {ordered.map((version) => (
                    <option key={version.id} value={version.id}>
                      v{version.version}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-xs">
                <span className="block text-slate-500">目标版本</span>
                <select
                  className="h-8 min-w-28 rounded-md border border-slate-200 bg-white px-2 dark:border-slate-700 dark:bg-slate-900"
                  value={toVersionId ?? ''}
                  onChange={(event) => {
                    setCompareRequested(false);
                    setToVersionId(Number(event.target.value) || null);
                  }}
                >
                  {ordered.map((version) => (
                    <option key={version.id} value={version.id}>
                      v{version.version}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                size="sm"
                variant="secondary"
                leftIcon={<GitCompareArrows className="h-4 w-4" />}
                disabled={
                  !fromVersionId
                  || !toVersionId
                  || fromVersionId === toVersionId
                  || diffQuery.isFetching
                }
                onClick={() => setCompareRequested(true)}
              >
                比较
              </Button>
            </div>

            {diffQuery.isFetching ? (
              <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                正在读取版本差异
              </div>
            ) : diffQuery.error ? (
              <p className="mt-4 text-sm text-red-600">
                {getApiErrorMessage(diffQuery.error, '读取版本差异失败')}
              </p>
            ) : compareRequested ? (
              <div className="mt-4 space-y-3">
                <div className="flex gap-2 text-xs">
                  <Badge variant="warning">change {grouped.counts.change}</Badge>
                  <Badge variant="success">add {grouped.counts.add}</Badge>
                  <Badge variant="danger">remove {grouped.counts.remove}</Badge>
                  {grouped.counts.unknown ? (
                    <Badge variant="secondary">
                      unknown {grouped.counts.unknown}
                    </Badge>
                  ) : null}
                </div>
                {!grouped.counts.total ? (
                  <p className="py-8 text-center text-sm text-slate-500">
                    两个版本的执行计划相同
                  </p>
                ) : grouped.groups.map((group) => (
                  <div key={group.kind} className="space-y-1">
                    <h3 className="text-xs font-semibold uppercase text-slate-500">
                      {group.kind}
                    </h3>
                    {group.changes.map((change, index) => (
                      <div
                        key={`${change.path}-${index}`}
                        className="rounded border border-slate-200 px-3 py-2 font-mono text-xs dark:border-slate-800"
                      >
                        <p className="break-all font-medium">{change.path}</p>
                        {change.kind !== 'add' ? (
                          <p className="mt-1 break-all text-red-600">
                            - {formatVersionChangeValue(change.before)}
                          </p>
                        ) : null}
                        {change.kind !== 'remove' ? (
                          <p className="mt-1 break-all text-emerald-700 dark:text-emerald-400">
                            + {formatVersionChangeValue(change.after)}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-10 text-center text-sm text-slate-500">
                选择两个版本后查看步骤、变量和执行策略差异
              </p>
            )}
          </section>
        </div>

        {rollbackTarget ? (
          <div className="border-t border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <div className="flex flex-wrap items-center gap-2">
              <RotateCcw className="h-4 w-4" />
              <span>
                确认将 active version 切换为 v{rollbackTarget.version}？历史版本不会被修改。
              </span>
              <div className="ml-auto flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setRollbackTarget(null)}
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={() => void confirmRollback()}
                  disabled={rollbackMutation.isPending}
                  leftIcon={rollbackMutation.isPending
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <RotateCcw className="h-4 w-4" />}
                >
                  确认回滚
                </Button>
              </div>
            </div>
            {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
          </div>
        ) : error ? (
          <p className="border-t border-red-200 px-4 py-2 text-xs text-red-600">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default WorkflowVersionDiff;

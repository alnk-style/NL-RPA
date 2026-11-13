import { DataPreview } from './DataPreview';
import type { WorkflowDraftExecutionResult } from '../../types/workflow';

type DrawerTab = 'logs' | 'screenshot' | 'variables' | 'data';

export function RunDrawer({
  result,
  tab,
  onTabChange,
}: {
  result: WorkflowDraftExecutionResult | null;
  tab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
}) {
  const tabs: Array<{ id: DrawerTab; label: string }> = [
    { id: 'logs', label: '日志' },
    { id: 'screenshot', label: '截图与产物' },
    { id: 'variables', label: '变量' },
    { id: 'data', label: '数据' },
  ];
  return (
    <div className="h-full">
      <div className="flex items-center gap-1 border-b border-slate-200 px-3 py-1.5 dark:border-slate-800">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`rounded px-2 py-1 text-xs ${
              tab === item.id
                ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-950'
                : 'text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
            onClick={() => onTabChange(item.id)}
          >
            {item.label}
          </button>
        ))}
        {result ? (
          <span className={`ml-auto text-xs ${result.success ? 'text-emerald-600' : 'text-red-600'}`}>
            {result.mode} · {result.status} · {result.duration_seconds.toFixed(2)}s
          </span>
        ) : null}
      </div>
      <div className="overflow-auto p-3">
        {!result ? (
          <p className="py-8 text-center text-sm text-slate-500">尚未试运行</p>
        ) : tab === 'logs' ? (
          <pre className="whitespace-pre-wrap font-mono text-xs">
            {JSON.stringify(result.step_logs, null, 2)}
          </pre>
        ) : tab === 'screenshot' ? (
          result.artifacts.length ? (
            <div className="space-y-2">
              {result.artifacts.map((artifact, index) => (
                <div key={index} className="rounded border border-slate-200 p-2 text-xs dark:border-slate-800">
                  <p className="font-medium">{artifact.name ?? artifact.path ?? `产物 ${index + 1}`}</p>
                  <p className="mt-1 break-all text-slate-500">{artifact.path}</p>
                </div>
              ))}
            </div>
          ) : <p className="text-sm text-slate-500">本次试运行没有产物</p>
        ) : tab === 'variables' ? (
          <pre className="whitespace-pre-wrap font-mono text-xs">
            {JSON.stringify(result.variables, null, 2)}
          </pre>
        ) : (
          <DataPreview
            value={result.variables.data ?? result.variables.result ?? []}
            stepLogs={result.step_logs}
          />
        )}
      </div>
    </div>
  );
}

export default RunDrawer;

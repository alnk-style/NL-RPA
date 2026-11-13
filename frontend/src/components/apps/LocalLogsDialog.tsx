import {
  FileText,
  LoaderCircle,
  RefreshCw,
  X,
} from 'lucide-react';

import type { LocalAppInfo } from '../../types/localApps';
import { Button } from '../ui/Button';

export interface LocalLogsDialogProps {
  app: LocalAppInfo;
  logs: string;
  loading: boolean;
  lines: number;
  embedded?: boolean;
  onLinesChange: (lines: number) => void;
  onRefresh: () => void;
  onClose: () => void;
}

export function LocalLogsDialog({
  app,
  logs,
  loading,
  lines,
  embedded = false,
  onLinesChange,
  onRefresh,
  onClose,
}: LocalLogsDialogProps) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/55 p-3 sm:p-5">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-logs-title"
        data-testid="local-logs-dialog"
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900"
      >
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <div className="flex min-w-0 items-start gap-2">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            <div className="min-w-0">
              <h2
                id="local-logs-title"
                className="break-words text-base font-semibold text-slate-950 dark:text-white"
              >
                {app.name} 运行日志
              </h2>
              <p className="mt-1 break-all text-xs text-slate-500">
                仅显示最近日志，敏感字段已脱敏。
              </p>
            </div>
          </div>
          <button
            type="button"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
            onClick={onClose}
            data-testid="local-logs-close"
            aria-label="关闭日志"
            title="关闭日志"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2 dark:border-slate-800 dark:bg-slate-950/40">
          <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            显示行数
            <select
              value={lines}
              onChange={(event) => onLinesChange(Number(event.target.value))}
              className="h-8 rounded-md border border-slate-200 bg-white px-2 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900"
              disabled={loading}
            >
              <option value={100}>最近 100 行</option>
              <option value={200}>最近 200 行</option>
              <option value={500}>最近 500 行</option>
            </select>
          </label>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              loading ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )
            }
            disabled={loading}
            onClick={onRefresh}
          >
            刷新
          </Button>
        </div>

        <pre
          data-testid="local-logs-content"
          className="min-h-64 flex-1 overflow-auto whitespace-pre-wrap break-words bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100"
        >
          {loading
            ? '正在读取日志...'
            : logs ||
              (embedded
                ? '当前应用以内嵌方式运行，暂无可用的独立进程日志。'
                : '暂无日志。应用启动后，标准输出和错误输出会显示在这里。')}
        </pre>
      </div>
    </div>
  );
}

export default LocalLogsDialog;

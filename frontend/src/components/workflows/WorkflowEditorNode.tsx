import {
  Bell,
  Boxes,
  Camera,
  Database,
  GitCompare,
  Keyboard,
  MousePointerClick,
  Navigation,
  Play,
  TextCursorInput,
  Timer,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import {
  Handle,
  Position,
  type NodeProps,
} from 'react-flow-renderer';

import { cn } from '../../lib/utils';
import { getActionPresentation } from '../../lib/workflow-editor';

export interface WorkflowEditorNodeData {
  kind: 'trigger' | 'step';
  label: string;
  action: string;
  executor?: string;
  appName?: string;
  toolName?: string;
  detail?: string;
  stepIndex: number;
}

const EXECUTOR_LABELS: Record<string, string> = {
  web: '网页',
  windows: '桌面',
  mcp: 'MCP',
  code: '代码',
};

const ACTION_ICONS: Record<string, LucideIcon> = {
  navigate: Navigation,
  goto: Navigation,
  click: MousePointerClick,
  fill: TextCursorInput,
  extract: Database,
  compare: GitCompare,
  notify: Bell,
  wait: Timer,
  press: Keyboard,
  screenshot: Camera,
};

function ActionIcon({
  action,
  className,
}: {
  action: string;
  className?: string;
}) {
  const Icon = ACTION_ICONS[action] ?? Play;
  return <Icon className={className} />;
}

export function WorkflowEditorNode({
  data,
  selected,
  isConnectable,
}: NodeProps<WorkflowEditorNodeData>) {
  const isTrigger = data.kind === 'trigger';
  const isMCP = data.executor === 'mcp' || data.action === 'call_tool';
  const presentation = getActionPresentation(data.action);

  return (
    <div
      className={cn(
        'w-[248px] cursor-grab rounded-md border-2 bg-white shadow-sm transition-shadow active:cursor-grabbing',
        isTrigger
          ? 'border-brand-300 bg-brand-50'
          : presentation.nodeClass,
        selected && 'shadow-md ring-2 ring-brand-500 ring-offset-2',
      )}
      title={isTrigger ? '流程触发节点' : '双击打开步骤配置'}
    >
      {!isTrigger ? (
        <Handle
          type="target"
          position={Position.Top}
          isConnectable={isConnectable}
          className={cn(
            '!h-3 !w-3 !border-2 !border-white',
            presentation.handleClass,
          )}
        />
      ) : null}

      <div className="flex items-start gap-2.5 px-3 py-3">
        <div
          className={cn(
            'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
            isTrigger
              ? 'bg-brand-600 text-white'
              : 'bg-white/80 text-slate-700 shadow-sm',
          )}
        >
          {isTrigger ? (
            <Zap className="h-4 w-4" />
          ) : isMCP ? (
            <Boxes className="h-4 w-4" />
          ) : (
            <ActionIcon action={data.action} className="h-4 w-4" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                {isTrigger
                  ? '触发条件'
                  : isMCP
                    ? `MCP · ${data.appName || '应用'}`
                    : `步骤 ${data.stepIndex + 1}`}
              </p>
              <p className="mt-1 text-sm font-semibold leading-5 text-slate-900">
                {isTrigger
                  ? data.label
                  : isMCP
                    ? data.toolName || '未选择工具'
                    : presentation.label}
              </p>
            </div>
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold',
                isTrigger
                  ? 'bg-brand-100 text-brand-700'
                  : presentation.badgeClass,
              )}
            >
              {isTrigger
                ? 'TRIGGER'
                : isMCP
                  ? 'MCP'
                  : `${EXECUTOR_LABELS[data.executor ?? 'web'] ?? data.executor} · ${data.action || 'CUSTOM'}`}
            </span>
          </div>

          {data.detail ? (
            <p className="mt-2 max-h-10 overflow-hidden break-all text-[11px] leading-4 text-slate-600">
              {data.detail}
            </p>
          ) : null}

          {!isTrigger ? (
            <p className="mt-2 text-[10px] text-slate-400">
              {isMCP
                ? `步骤 ${data.stepIndex + 1}`
                : presentation.description}
            </p>
          ) : null}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={isConnectable}
        className={cn(
          '!h-3 !w-3 !border-2 !border-white',
          isTrigger ? '!bg-brand-500' : presentation.handleClass,
        )}
      />
    </div>
  );
}

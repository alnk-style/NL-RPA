import {
  ArrowLeft,
  Code2,
  History,
  LoaderCircle,
  Play,
  Redo2,
  Rocket,
  Save,
  Sparkles,
  Undo2,
  Video,
} from 'lucide-react';

import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { workflowModeLabel } from '../../lib/workflow-mode';
import type { WorkflowRecord } from '../../types/workflow';

export function StudioTopBar({
  workflow,
  dirty,
  saving,
  publishing,
  running,
  canUndo,
  canRedo,
  showAdvanced,
  onBack,
  onSave,
  onRun,
  onPublish,
  onVersions,
  onDraft,
  onRecording,
  onUndo,
  onRedo,
  onToggleAdvanced,
}: {
  workflow: WorkflowRecord;
  dirty: boolean;
  saving: boolean;
  publishing: boolean;
  running: boolean;
  canUndo: boolean;
  canRedo: boolean;
  showAdvanced: boolean;
  onBack: () => void;
  onSave: () => void;
  onRun: () => void;
  onPublish: () => void;
  onVersions: () => void;
  onDraft: () => void;
  onRecording: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onToggleAdvanced: () => void;
}) {
  const mode = workflowModeLabel(workflow);
  const executionPolicy = workflow.execution_policy ?? {};
  const runModeLabel: Record<string, string> = {
    interactive_preferred: '允许交互桌面',
    background_if_safe: '仅后台安全',
    isolated_preferred: '优先 VM',
  };
  const hostPolicyLabel: Record<string, string> = {
    local_only: '仅本机',
    local_then_isolated: '本机优先',
    isolated_only: '仅 VM',
  };
  const runMode = String(executionPolicy.mode ?? '');
  const hostPolicy = String(executionPolicy.host_policy ?? '');
  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
      <Button
        variant="ghost"
        size="sm"
        aria-label="返回工作流列表"
        title="返回"
        leftIcon={<ArrowLeft className="h-4 w-4" />}
        onClick={onBack}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-sm font-semibold">{workflow.name}</h1>
          <Badge variant={mode.mode === 'legacy_code' ? 'secondary' : 'success'}>
            {mode.label}
          </Badge>
          <span className="font-mono text-xs text-slate-500">{mode.versionLabel}</span>
          {runMode ? (
            <Badge variant="outline">
              {runModeLabel[runMode] ?? runMode}
            </Badge>
          ) : null}
          {hostPolicy ? (
            <Badge variant="outline">
              {hostPolicyLabel[hostPolicy] ?? hostPolicy}
            </Badge>
          ) : null}
          {dirty ? <Badge variant="warning">未保存</Badge> : null}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        title="撤销"
        aria-label="撤销"
        leftIcon={<Undo2 className="h-4 w-4" />}
        disabled={!canUndo}
        onClick={onUndo}
      />
      <Button
        variant="ghost"
        size="sm"
        title="重做"
        aria-label="重做"
        leftIcon={<Redo2 className="h-4 w-4" />}
        disabled={!canRedo}
        onClick={onRedo}
      />
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<History className="h-4 w-4" />}
        onClick={onVersions}
      >
        版本
      </Button>
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<Code2 className="h-4 w-4" />}
        onClick={onToggleAdvanced}
      >
        {showAdvanced ? '关闭高级' : '高级'}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<Sparkles className="h-4 w-4" />}
        onClick={onDraft}
        disabled={mode.readOnly}
      >
        描述生成
      </Button>
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<Video className="h-4 w-4" />}
        onClick={onRecording}
        disabled={mode.readOnly}
      >
        录制
      </Button>
      <Button
        size="sm"
        variant="secondary"
        leftIcon={saving
          ? <LoaderCircle className="h-4 w-4 animate-spin" />
          : <Save className="h-4 w-4" />}
        onClick={onSave}
        disabled={saving || mode.readOnly}
      >
        保存
      </Button>
      <Button
        size="sm"
        variant="secondary"
        leftIcon={running
          ? <LoaderCircle className="h-4 w-4 animate-spin" />
          : <Play className="h-4 w-4" />}
        onClick={onRun}
        disabled={running || mode.readOnly}
      >
        试运行
      </Button>
      <Button
        size="sm"
        leftIcon={publishing
          ? <LoaderCircle className="h-4 w-4 animate-spin" />
          : <Rocket className="h-4 w-4" />}
        onClick={onPublish}
        disabled={saving || publishing || dirty || mode.readOnly}
      >
        发布
      </Button>
    </header>
  );
}

export default StudioTopBar;

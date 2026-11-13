import {
  Copy,
  LoaderCircle,
  Pencil,
  Play,
  Power,
  Trash2,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { workflowModeLabel } from '../../lib/workflow-mode';
import type { WorkflowRecord } from '../../types/workflow';

export function WorkflowRow({
  workflow,
  busyAction,
  onDuplicate,
  onDelete,
  onRun,
  onTogglePublished,
}: {
  workflow: WorkflowRecord;
  busyAction: string | null;
  onDuplicate: (workflow: WorkflowRecord) => void;
  onDelete: (workflow: WorkflowRecord) => void;
  onRun: (workflow: WorkflowRecord) => void;
  onTogglePublished: (workflow: WorkflowRecord) => void;
}) {
  const navigate = useNavigate();
  const mode = workflowModeLabel(workflow);
  const busy = (suffix: string) => busyAction === `${workflow.id}:${suffix}`;
  return (
    <tr className="border-b border-slate-100 last:border-0 dark:border-slate-800">
      <td className="min-w-[240px] px-4 py-3">
        <Link
          to={`/workflows/${workflow.id}/studio`}
          className="font-medium text-slate-900 hover:text-brand-700 dark:text-white"
        >
          {workflow.name}
        </Link>
        <p className="mt-1 max-w-[480px] truncate text-xs text-slate-500">
          {workflow.description || workflow.natural_language_input}
        </p>
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <div className="flex items-center gap-2">
          <Badge
            variant={
              mode.warning
                ? 'warning'
                : mode.mode === 'legacy_code'
                  ? 'secondary'
                  : 'success'
            }
          >
            {mode.label}
          </Badge>
          <span className="font-mono text-xs text-slate-500">
            {mode.versionLabel}
          </span>
        </div>
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <Badge
          variant={workflow.status === 'published' ? 'success' : 'secondary'}
        >
          {workflow.status === 'published' ? '已发布' : '草稿'}
        </Badge>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">
        {workflow.trigger_type === 'cron'
          ? workflow.cron_expression || '定时'
          : '手动'}
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <div className="flex justify-end gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            title="编辑"
            aria-label={`编辑 ${workflow.name}`}
            leftIcon={<Pencil className="h-3.5 w-3.5" />}
            onClick={() => navigate(`/workflows/${workflow.id}/studio`)}
          >
            编辑
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="复制"
            aria-label={`复制 ${workflow.name}`}
            leftIcon={<Copy className="h-3.5 w-3.5" />}
            onClick={() => onDuplicate(workflow)}
            disabled={busy('duplicate')}
          />
          <Button
            variant="ghost"
            size="sm"
            title="运行"
            aria-label={`运行 ${workflow.name}`}
            leftIcon={
              busy('run')
                ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                : <Play className="h-3.5 w-3.5" />
            }
            onClick={() => onRun(workflow)}
            disabled={
              busy('run')
              || workflow.status !== 'published'
              || workflow.active_version_id === null
              || mode.readOnly
            }
          />
          <Button
            variant="ghost"
            size="sm"
            title={workflow.status === 'published' ? '停用' : '发布'}
            aria-label={
              workflow.status === 'published'
                ? `停用 ${workflow.name}`
                : `发布 ${workflow.name}`
            }
            leftIcon={<Power className="h-3.5 w-3.5" />}
            onClick={() => onTogglePublished(workflow)}
            disabled={busy('publish') || mode.readOnly}
          />
          <Button
            variant="ghost"
            size="sm"
            title="删除"
            aria-label={`删除 ${workflow.name}`}
            leftIcon={<Trash2 className="h-3.5 w-3.5" />}
            onClick={() => onDelete(workflow)}
            disabled={busy('delete')}
          />
        </div>
      </td>
    </tr>
  );
}

export default WorkflowRow;

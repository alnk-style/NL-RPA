import { useState } from 'react';
import { Download, FileText, LoaderCircle } from 'lucide-react';

import { getExecutionArtifact } from '../../api/executions';
import { getApiErrorMessage } from '../../lib/errors';
import type { ExecutionArtifact } from '../../types/workflow';
import { Button } from '../ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';

function formatFileSize(size: unknown): string | null {
  if (typeof size !== 'number' || !Number.isFinite(size) || size < 0) {
    return null;
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function ArtifactRow({
  executionId,
  artifact,
  index,
}: {
  executionId: number;
  artifact: ExecutionArtifact;
  index: number;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = artifact.name?.trim() || `文件 ${index + 1}`;
  const size = formatFileSize(artifact.size);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const blob = await getExecutionArtifact(executionId, index);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (downloadError) {
      setError(getApiErrorMessage(downloadError, '下载文件失败'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          <FileText className="h-4 w-4 text-slate-400" />
          <span className="break-all">{name}</span>
          {size ? <span className="text-xs text-slate-400">{size}</span> : null}
        </p>
        <p className="mt-1 break-all text-xs text-slate-500">
          {artifact.delivered && artifact.delivered_path
            ? `已保存到 ${artifact.delivered_path}`
            : artifact.error ?? '保存在执行记录中，可直接下载。'}
        </p>
        {error ? <p className="mt-1 text-xs text-red-600">{error}</p> : null}
      </div>
      <Button
        variant="secondary"
        size="sm"
        disabled={busy}
        leftIcon={busy
          ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          : <Download className="h-3.5 w-3.5" />}
        onClick={() => void download()}
      >
        下载
      </Button>
    </li>
  );
}

export function ArtifactPanel({
  executionId,
  artifacts,
}: {
  executionId: number;
  artifacts: ExecutionArtifact[];
}) {
  if (!artifacts.length) {
    return null;
  }
  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle>输出文件</CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          {artifacts.length} 个交付产物
        </p>
      </CardHeader>
      <CardContent className="p-0">
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {artifacts.map((artifact, index) => (
            <ArtifactRow
              key={`${artifact.name ?? 'artifact'}-${index}`}
              executionId={executionId}
              artifact={artifact}
              index={index}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export default ArtifactPanel;

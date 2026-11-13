import { ExternalLink, Server, TriangleAlert } from 'lucide-react';

import {
  hostStatusLabel,
  vmAccessFromHost,
} from '../../lib/hosts';
import { redactSensitiveText } from '../../lib/display';
import type { HostRecord } from '../../types/hosts';
import { Badge } from '../ui/Badge';

export function VmHumanTaskDetails({
  host,
  loading,
}: {
  host: HostRecord | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/40">
        正在读取执行主机状态。
      </div>
    );
  }
  if (!host || host.environment !== 'vm') {
    return null;
  }
  const access = vmAccessFromHost(host);
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs dark:border-slate-800 dark:bg-slate-950/40">
      <div className="flex flex-wrap items-center gap-2">
        <Server className="h-4 w-4 text-slate-500" />
        <span className="font-medium text-slate-700 dark:text-slate-200">
          {host.name}
        </span>
        <Badge
          variant={
            host.status === 'online'
              ? 'success'
              : host.status === 'busy'
                ? 'warning'
                : 'secondary'
          }
        >
          {hostStatusLabel(host.status)}
        </Badge>
        <Badge variant="outline">VM</Badge>
      </div>
      {host.status !== 'online' ? (
        <p className="mt-2 flex items-start gap-1.5 leading-5 text-amber-700 dark:text-amber-300">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          原执行主机当前{hostStatusLabel(host.status)}，不能恢复 host-bound 执行。
        </p>
      ) : null}
      {access.url ? (
        <a
          href={access.url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex items-center gap-1.5 font-medium text-brand-700 hover:underline dark:text-brand-300"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          打开 VM 控制台
        </a>
      ) : null}
      {access.instructions ? (
        <p className="mt-2 break-words leading-5 text-slate-500 dark:text-slate-400">
          {redactSensitiveText(access.instructions)}
        </p>
      ) : null}
      {!access.hasAccess ? (
        <p className="mt-2 leading-5 text-slate-500">
          管理员尚未配置 VM 控制台链接或人工访问说明。
        </p>
      ) : null}
    </div>
  );
}

export default VmHumanTaskDetails;

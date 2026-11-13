import { useState } from 'react';
import {
  ExternalLink,
  Monitor,
  Save,
  Server,
  TriangleAlert,
} from 'lucide-react';

import {
  hostEnvironmentLabel,
  hostStatusLabel,
  vmAccessFromHost,
} from '../../lib/hosts';
import { redactSensitiveText } from '../../lib/display';
import type { HostRecord } from '../../types/hosts';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

interface VmAccessPanelProps {
  hosts: HostRecord[];
  savingHostId: number | null;
  onSave: (
    hostId: number,
    payload: {
      human_access_url: string | null;
      human_access_instructions: string | null;
    },
  ) => Promise<void>;
}

function statusVariant(status: HostRecord['status']) {
  if (status === 'online') {
    return 'success' as const;
  }
  if (status === 'busy') {
    return 'warning' as const;
  }
  return 'secondary' as const;
}

export function VmAccessPanel({
  hosts,
  savingHostId,
  onSave,
}: VmAccessPanelProps) {
  const [editing, setEditing] = useState<Record<number, boolean>>({});
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [instructions, setInstructions] = useState<Record<number, string>>(
    {},
  );

  if (!hosts.length) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 px-5 py-10 text-center dark:border-slate-700">
        <Server className="mx-auto h-6 w-6 text-slate-400" />
        <p className="mt-3 text-sm font-medium text-slate-700 dark:text-slate-200">
          还没有注册执行主机
        </p>
        <p className="mt-1 text-xs text-slate-500">
          VM 需要在目标机器上手动安装 Agent，并使用现有配对流程注册。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {hosts.map((host) => {
        const access = vmAccessFromHost(host);
        const isVm = host.environment === 'vm';
        const isEditing = Boolean(editing[host.host_id]);
        const urlValue = urls[host.host_id]
          ?? host.human_access_info?.url
          ?? '';
        const instructionValue = instructions[host.host_id]
          ?? host.human_access_info?.instructions
          ?? '';
        return (
          <article
            key={host.host_id}
            className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {isVm ? (
                    <Server className="h-4 w-4 text-slate-500" />
                  ) : (
                    <Monitor className="h-4 w-4 text-slate-500" />
                  )}
                  <h3 className="break-words text-sm font-semibold text-slate-900 dark:text-white">
                    {host.name}
                  </h3>
                  <Badge variant="outline">
                    {hostEnvironmentLabel(host.environment)}
                  </Badge>
                  <Badge variant={statusVariant(host.status)}>
                    {hostStatusLabel(host.status)}
                  </Badge>
                </div>
                <p className="mt-2 break-words text-xs text-slate-500">
                  Agent {host.agent_version} · {host.platform} ·
                  活动任务 {host.active_task_count}
                </p>
                <p className="mt-1 break-words text-xs text-slate-500">
                  能力：
                  {host.capabilities.length
                    ? host.capabilities.join('、')
                    : '最小能力，未声明桌面控制'}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  传输：{host.connection_security || 'unknown'}
                  {host.last_seen_at
                    ? ` · 最近心跳 ${new Date(host.last_seen_at).toLocaleString()}`
                    : ''}
                </p>
              </div>
              {isVm && access.url ? (
                <a
                  href={access.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 px-2.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  打开控制台
                </a>
              ) : null}
            </div>

            {isVm ? (
              <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
                {isEditing ? (
                  <div className="space-y-3">
                    <label className="block space-y-1.5">
                      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
                        受信控制台 URL
                      </span>
                      <input
                        className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-950"
                        value={urlValue}
                        onChange={(event) => {
                          const value = event.target.value;
                          setUrls((current) => ({
                            ...current,
                            [host.host_id]: value,
                          }));
                        }}
                        placeholder="https://console.example.com/vm/1"
                      />
                    </label>
                    <label className="block space-y-1.5">
                      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
                        人工访问说明
                      </span>
                      <textarea
                        className="min-h-20 w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
                        value={instructionValue}
                        onChange={(event) => {
                          const value = event.target.value;
                          setInstructions((current) => ({
                            ...current,
                            [host.host_id]: value,
                          }));
                        }}
                        placeholder="例如：通过受信控制台登录 VM，然后回到待处理中心继续。"
                      />
                    </label>
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          setEditing((current) => ({
                            ...current,
                            [host.host_id]: false,
                          }))
                        }
                      >
                        取消
                      </Button>
                      <Button
                        size="sm"
                        leftIcon={<Save className="h-3.5 w-3.5" />}
                        disabled={savingHostId === host.host_id}
                        onClick={() => {
                          void onSave(host.host_id, {
                            human_access_url: urlValue.trim() || null,
                            human_access_instructions:
                              instructionValue.trim() || null,
                          }).then(() => {
                            setEditing((current) => ({
                              ...current,
                              [host.host_id]: false,
                            }));
                          });
                        }}
                      >
                        {savingHostId === host.host_id ? '保存中' : '保存'}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 text-xs leading-5 text-slate-500">
                      {access.hasAccess ? (
                        <>
                          {access.instructions ? (
                            <p className="break-words">
                              {redactSensitiveText(access.instructions)}
                            </p>
                          ) : null}
                          {!access.url ? (
                            <p className="mt-1">
                              未配置控制台链接，请按上述说明人工访问 VM。
                            </p>
                          ) : null}
                        </>
                      ) : (
                        <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-300">
                          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          尚未配置人工访问方式。
                        </p>
                      )}
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() =>
                        setEditing((current) => ({
                          ...current,
                          [host.host_id]: true,
                        }))
                      }
                    >
                      编辑访问方式
                    </Button>
                  </div>
                )}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

export default VmAccessPanel;

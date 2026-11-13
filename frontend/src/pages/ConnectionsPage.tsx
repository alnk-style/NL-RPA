import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FolderCheck,
  Globe2,
  Eye,
  KeyRound,
  LoaderCircle,
  MailCheck,
  RefreshCw,
  Server,
} from 'lucide-react';

import {
  createBrowserProfile,
  getExportDirectories,
  getVisualSettings,
  listBrowserProfiles,
  testVisualProvider,
  updateExportDirectories,
  updateVisualSettings,
} from '../api/connections';
import { listHosts, updateHostAccess } from '../api/hosts';
import { queryKeys } from '../api/queryKeys';
import {
  getEmailSettings,
  sendTestEmail,
  testEmailConnection,
  updateEmailSettings,
} from '../api/settings';
import { CredentialEditor } from '../components/connections/CredentialEditor';
import { ExportDirectoryForm } from '../components/connections/ExportDirectoryForm';
import { ProfileList } from '../components/connections/ProfileList';
import { SmtpSettingsForm } from '../components/connections/SmtpSettingsForm';
import { VisualSettingsForm } from '../components/connections/VisualSettingsForm';
import { VmAccessPanel } from '../components/connections/VmAccessPanel';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { buildEmailSettingsUpdate } from '../lib/connections';
import { buildHostEventsWebSocketUrl } from '../lib/hosts';
import { getApiErrorMessage } from '../lib/errors';
import type {
  EmailDeliveryResult,
  VisualProviderTestResult,
} from '../types/connections';

type ConnectionTab =
  | 'smtp'
  | 'browser'
  | 'visual'
  | 'hosts'
  | 'exports'
  | 'credentials';

const TABS: Array<{
  id: ConnectionTab;
  label: string;
  icon: typeof MailCheck;
}> = [
  { id: 'smtp', label: 'SMTP 邮件', icon: MailCheck },
  { id: 'browser', label: '浏览器 Profile', icon: Globe2 },
  { id: 'visual', label: '视觉兜底', icon: Eye },
  { id: 'hosts', label: '执行主机', icon: Server },
  { id: 'exports', label: '导出目录', icon: FolderCheck },
  { id: 'credentials', label: '凭据', icon: KeyRound },
];

export function ConnectionsPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<ConnectionTab>('smtp');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [sendingTest, setSendingTest] = useState(false);
  const [visualTesting, setVisualTesting] = useState(false);
  const [savingHostId, setSavingHostId] = useState<number | null>(null);

  const emailQuery = useQuery({
    queryKey: queryKeys.emailSettings,
    queryFn: getEmailSettings,
  });
  const profilesQuery = useQuery({
    queryKey: queryKeys.browserProfiles,
    queryFn: listBrowserProfiles,
  });
  const exportQuery = useQuery({
    queryKey: ['settings', 'export-directories'],
    queryFn: getExportDirectories,
  });
  const visualQuery = useQuery({
    queryKey: queryKeys.visualSettings,
    queryFn: getVisualSettings,
  });
  const hostsQuery = useQuery({
    queryKey: queryKeys.hosts,
    queryFn: listHosts,
    refetchInterval: 5000,
  });
  const emailMutation = useMutation({
    mutationFn: updateEmailSettings,
  });
  const profileMutation = useMutation({
    mutationFn: createBrowserProfile,
  });
  const exportMutation = useMutation({
    mutationFn: updateExportDirectories,
  });
  const visualMutation = useMutation({
    mutationFn: updateVisualSettings,
  });
  const hostMutation = useMutation({
    mutationFn: ({
      hostId,
      payload,
    }: {
      hostId: number;
      payload: {
        human_access_url: string | null;
        human_access_instructions: string | null;
      };
    }) => updateHostAccess(hostId, payload),
  });

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let closed = false;
    const connect = () => {
      socket = new WebSocket(buildHostEventsWebSocketUrl());
      socket.onmessage = () => {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.hosts,
        });
      };
      socket.onclose = () => {
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [queryClient]);

  async function saveEmail(
    form: Parameters<typeof buildEmailSettingsUpdate>[0],
  ) {
    setError(null);
    setNotice(null);
    try {
      await emailMutation.mutateAsync(buildEmailSettingsUpdate(form));
      await queryClient.invalidateQueries({
        queryKey: queryKeys.emailSettings,
      });
      setNotice('SMTP 设置已保存。');
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, '保存 SMTP 设置失败'));
    }
  }

  async function createProfile(payload: {
    name: string;
    user_data_dir?: string | null;
    profile_directory?: string;
  }) {
    setError(null);
    setNotice(null);
    try {
      await profileMutation.mutateAsync(payload);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.browserProfiles,
      });
      setNotice('浏览器 Profile 已创建。');
    } catch (createError) {
      setError(getApiErrorMessage(createError, '创建浏览器 Profile 失败'));
    }
  }

  async function saveExports(directories: string[]) {
    setError(null);
    setNotice(null);
    try {
      await exportMutation.mutateAsync(directories);
      await queryClient.invalidateQueries({
        queryKey: ['settings', 'export-directories'],
      });
      setNotice('导出目录已保存。');
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, '保存导出目录失败'));
    }
  }

  async function testConnection(): Promise<EmailDeliveryResult> {
    setTesting(true);
    try {
      return await testEmailConnection();
    } catch (testError) {
      return {
        success: false,
        status: 'failed',
        error: getApiErrorMessage(testError, 'SMTP 连接测试失败'),
      };
    } finally {
      setTesting(false);
    }
  }

  async function sendTest(recipient: string): Promise<EmailDeliveryResult> {
    setSendingTest(true);
    try {
      return await sendTestEmail(recipient);
    } catch (testError) {
      return {
        success: false,
        status: 'failed',
        error: getApiErrorMessage(testError, '测试邮件发送失败'),
      };
    } finally {
      setSendingTest(false);
    }
  }

  async function saveVisual(payload: Parameters<
    typeof updateVisualSettings
  >[0]) {
    setError(null);
    setNotice(null);
    try {
      await visualMutation.mutateAsync(payload);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.visualSettings,
      });
      setNotice('视觉设置已保存。');
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, '保存视觉设置失败'));
    }
  }

  async function testVisual(): Promise<VisualProviderTestResult> {
    setVisualTesting(true);
    try {
      return await testVisualProvider();
    } catch (testError) {
      return {
        status: 'failed',
        provider: 'unknown',
        message: getApiErrorMessage(testError, '视觉自检失败'),
      };
    } finally {
      setVisualTesting(false);
    }
  }

  async function saveHostAccess(
    hostId: number,
    payload: {
      human_access_url: string | null;
      human_access_instructions: string | null;
    },
  ) {
    setSavingHostId(hostId);
    setError(null);
    setNotice(null);
    try {
      await hostMutation.mutateAsync({ hostId, payload });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.hosts,
      });
      setNotice('VM 人工访问方式已更新。');
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, '保存主机访问方式失败'));
    } finally {
      setSavingHostId(null);
    }
  }

  const loading = (
    emailQuery.isLoading
    || profilesQuery.isLoading
    || exportQuery.isLoading
    || visualQuery.isLoading
    || hostsQuery.isLoading
  );
  const queryError = (
    emailQuery.error
    || profilesQuery.error
    || exportQuery.error
    || visualQuery.error
    || hostsQuery.error
  );

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">连接与凭据</h1>
          <p className="mt-1 text-sm text-slate-500">
            SMTP、自动化浏览器 Profile、执行主机、导出目录和工作流凭据。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}
          disabled={loading}
          onClick={() => {
            void Promise.all([
              emailQuery.refetch(),
              profilesQuery.refetch(),
              exportQuery.refetch(),
              visualQuery.refetch(),
              hostsQuery.refetch(),
            ]);
          }}
        >
          刷新
        </Button>
      </header>

      {error || queryError ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
          {error ?? getApiErrorMessage(queryError, '加载连接配置失败')}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
          {notice}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[190px_minmax(0,1fr)]">
        <nav className="h-fit rounded-md border border-slate-200 bg-white p-1.5 dark:border-slate-800 dark:bg-slate-900">
          {TABS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm ${
                  tab === item.id
                    ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-950'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                }`}
                onClick={() => setTab(item.id)}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="min-w-0">
          {loading ? (
            <Card className="flex min-h-56 items-center justify-center">
              <LoaderCircle className="h-5 w-5 animate-spin text-slate-400" />
            </Card>
          ) : tab === 'smtp' && emailQuery.data ? (
            <SmtpSettingsForm
              settings={emailQuery.data}
              saving={emailMutation.isPending}
              testing={testing}
              sendingTest={sendingTest}
              onSave={saveEmail}
              onTest={testConnection}
              onSendTest={sendTest}
            />
          ) : tab === 'browser' ? (
            <ProfileList
              profiles={profilesQuery.data ?? []}
              creating={profileMutation.isPending}
              onCreate={createProfile}
            />
          ) : tab === 'exports' ? (
            <ExportDirectoryForm
              directories={exportQuery.data?.directories ?? []}
              saving={exportMutation.isPending}
              onSave={saveExports}
            />
          ) : tab === 'visual' && visualQuery.data ? (
            <VisualSettingsForm
              settings={visualQuery.data}
              saving={visualMutation.isPending}
              testing={visualTesting}
              onSave={saveVisual}
              onTest={testVisual}
            />
          ) : tab === 'hosts' ? (
            <VmAccessPanel
              hosts={hostsQuery.data ?? []}
              savingHostId={savingHostId}
              onSave={saveHostAccess}
            />
          ) : tab === 'credentials' ? (
            <CredentialEditor />
          ) : null}
        </div>
      </div>
    </section>
  );
}

export default ConnectionsPage;

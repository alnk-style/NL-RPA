import api from './axios';
import type {
  GeneratedInstalledAppResponse,
  LocalAppInfo,
  LocalAppBinding,
  LocalAppListResponse,
  LocalAppLogsResponse,
  LocalAppOperationResponse,
  LocalAppScanResponse,
  LocalAppStopResponse,
} from '../types/localApps';
import type {
  DiscoverableInstalledAppsResponse,
} from '../lib/installed-app-mcp';

export async function listLocalApps(): Promise<LocalAppListResponse> {
  const response = await api.get<LocalAppListResponse>('/api/local-apps');
  return response.data;
}

export async function scanLocalApps(): Promise<LocalAppScanResponse> {
  const response = await api.post<LocalAppScanResponse>(
    '/api/local-apps/scan',
  );
  return response.data;
}

export async function startLocalApp(
  appId: string,
): Promise<LocalAppOperationResponse> {
  const response = await api.post<LocalAppOperationResponse>(
    `/api/local-apps/${encodeURIComponent(appId)}/start`,
  );
  return response.data;
}

export async function stopLocalApp(
  appId: string,
): Promise<LocalAppStopResponse> {
  const response = await api.post<LocalAppStopResponse>(
    `/api/local-apps/${encodeURIComponent(appId)}/stop`,
  );
  return response.data;
}

export async function restartLocalApp(
  appId: string,
): Promise<LocalAppOperationResponse> {
  const response = await api.post<LocalAppOperationResponse>(
    `/api/local-apps/${encodeURIComponent(appId)}/restart`,
  );
  return response.data;
}

export async function getLocalAppLogs(
  appId: string,
  lines = 200,
): Promise<LocalAppLogsResponse> {
  const response = await api.get<LocalAppLogsResponse>(
    `/api/local-apps/${encodeURIComponent(appId)}/logs`,
    { params: { lines } },
  );
  return response.data;
}

export async function getLocalApp(
  appId: string,
): Promise<LocalAppInfo> {
  const response = await api.get<{ app: LocalAppInfo }>(
    `/api/local-apps/${encodeURIComponent(appId)}`,
  );
  return response.data.app;
}

export async function getLocalAppBinding(
  appId: string,
): Promise<LocalAppBinding> {
  const response = await api.get<LocalAppBinding>(
    `/api/local-apps/${encodeURIComponent(appId)}/binding`,
  );
  return response.data;
}

export async function updateLocalAppBinding(
  appId: string,
  autoStart: boolean,
): Promise<LocalAppBinding> {
  const response = await api.patch<LocalAppBinding>(
    `/api/local-apps/${encodeURIComponent(appId)}/binding`,
    { auto_start: autoStart },
  );
  return response.data;
}

export async function removeLocalAppBinding(
  appId: string,
): Promise<LocalAppBinding> {
  const response = await api.delete<LocalAppBinding>(
    `/api/local-apps/${encodeURIComponent(appId)}/binding`,
  );
  return response.data;
}

export async function listDiscoverableApps(
  refresh = false,
): Promise<DiscoverableInstalledAppsResponse> {
  const response = await api.get<DiscoverableInstalledAppsResponse>(
    '/api/local-apps/discoverable-apps',
    { params: refresh ? { refresh: true } : undefined },
  );
  return response.data;
}

export async function createInstalledAppMCP(
  appKey: string,
  agentId: number,
): Promise<GeneratedInstalledAppResponse> {
  const response = await api.post<GeneratedInstalledAppResponse>(
    `/api/local-apps/discoverable-apps/${encodeURIComponent(appKey)}/create`,
    { agent_id: agentId },
  );
  return response.data;
}

export async function deleteGeneratedLocalApp(
  appId: string,
  options: {
    agentId?: number | null;
    removeFromAllowlist?: boolean;
  } = {},
): Promise<{ deleted: boolean; app_id: string }> {
  const response = await api.delete<{
    deleted: boolean;
    app_id: string;
  }>(
    `/api/local-apps/generated/${encodeURIComponent(appId)}`,
    {
      params: {
        agent_id: options.agentId,
        remove_from_allowlist:
          options.removeFromAllowlist ?? false,
      },
    },
  );
  return response.data;
}

import api from './axios';
import type {
  BrowserProfileRecord,
  ExportDirectorySettings,
  VisualProviderTestResult,
  VisualSettingsRecord,
  WorkflowCredentialKeys,
} from '../types/connections';

export async function listBrowserProfiles(): Promise<BrowserProfileRecord[]> {
  const response = await api.get<BrowserProfileRecord[]>(
    '/api/browser-profiles',
  );
  return response.data;
}

export async function createBrowserProfile(payload: {
  name: string;
  user_data_dir?: string | null;
  profile_directory?: string;
}): Promise<BrowserProfileRecord> {
  const response = await api.post<BrowserProfileRecord>(
    '/api/browser-profiles',
    payload,
  );
  return response.data;
}

export async function getExportDirectories(): Promise<ExportDirectorySettings> {
  const response = await api.get<ExportDirectorySettings>(
    '/api/settings/export-directories',
  );
  return response.data;
}

export async function updateExportDirectories(
  directories: string[],
): Promise<ExportDirectorySettings> {
  const response = await api.put<ExportDirectorySettings>(
    '/api/settings/export-directories',
    { directories },
  );
  return response.data;
}

export async function listCredentialKeys(
  workflowId: number,
): Promise<WorkflowCredentialKeys> {
  const response = await api.get<WorkflowCredentialKeys>(
    `/api/workflows/${workflowId}/credentials`,
  );
  return response.data;
}

export async function saveCredential(
  workflowId: number,
  key: string,
  value: string,
): Promise<{ status: string; workflow_id: number; key: string }> {
  const response = await api.put<{
    status: string;
    workflow_id: number;
    key: string;
  }>(
    `/api/workflows/${workflowId}/credentials/${encodeURIComponent(key)}`,
    { value },
  );
  return response.data;
}

export async function deleteCredential(
  workflowId: number,
  key: string,
): Promise<{ status: string; workflow_id: number; key: string }> {
  const response = await api.delete<{
    status: string;
    workflow_id: number;
    key: string;
  }>(
    `/api/workflows/${workflowId}/credentials/${encodeURIComponent(key)}`,
  );
  return response.data;
}

export async function getVisualSettings(): Promise<VisualSettingsRecord> {
  const response = await api.get<VisualSettingsRecord>(
    '/api/settings/visual',
  );
  return response.data;
}

export async function updateVisualSettings(payload: {
  enabled: boolean;
  provider: VisualSettingsRecord['provider'];
  endpoint: string;
  model: string;
  confidence_threshold: number;
  max_candidates: number;
  max_attempts: number;
  screenshot_scope: VisualSettingsRecord['screenshot_scope'];
  redaction_policy: Record<string, unknown>;
  api_key?: string | null;
}): Promise<VisualSettingsRecord> {
  const response = await api.put<VisualSettingsRecord>(
    '/api/settings/visual',
    payload,
  );
  return response.data;
}

export async function testVisualProvider(): Promise<VisualProviderTestResult> {
  const response = await api.post<VisualProviderTestResult>(
    '/api/settings/visual/test',
  );
  return response.data;
}

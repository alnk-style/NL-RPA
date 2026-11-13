import api from './axios';
import type {
  BrowserLoginSessionResponse,
  HumanTaskListResponse,
  HumanTaskRecord,
  HumanTaskResumeResponse,
} from '../types/humanTasks';

export async function listHumanTasks(params: {
  status?: string;
  kind?: string;
} = {}): Promise<HumanTaskListResponse> {
  const response = await api.get<HumanTaskListResponse>(
    '/api/human-tasks',
    { params },
  );
  return response.data;
}

export async function getHumanTask(
  humanTaskId: number,
): Promise<HumanTaskRecord> {
  const response = await api.get<HumanTaskRecord>(
    `/api/human-tasks/${humanTaskId}`,
  );
  return response.data;
}

export async function resumeHumanTask(
  humanTaskId: number,
  verification: Record<string, unknown> = {},
): Promise<HumanTaskResumeResponse> {
  const response = await api.post<HumanTaskResumeResponse>(
    `/api/human-tasks/${humanTaskId}/resume`,
    {
      decision: 'resume',
      verification,
    },
  );
  return response.data;
}

export async function cancelHumanTask(
  humanTaskId: number,
  reason = '用户取消了人工任务。',
): Promise<HumanTaskRecord> {
  const response = await api.post<HumanTaskRecord>(
    `/api/human-tasks/${humanTaskId}/cancel`,
    { reason },
  );
  return response.data;
}

export async function openBrowserProfileLogin(
  profileId: number,
  humanTaskId: number,
  url?: string | null,
): Promise<BrowserLoginSessionResponse> {
  const response = await api.post<BrowserLoginSessionResponse>(
    `/api/browser-profiles/${profileId}/login`,
    {
      human_task_id: humanTaskId,
      url: url || null,
    },
  );
  return response.data;
}

export async function getHumanTaskScreenshot(
  humanTaskId: number,
): Promise<Blob> {
  const response = await api.get<Blob>(
    `/api/human-tasks/${humanTaskId}/screenshot`,
    { responseType: 'blob' },
  );
  return response.data;
}

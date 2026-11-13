import api from './axios';
import type { HostListResponse, HostRecord } from '../types/hosts';

export async function listHosts(): Promise<HostRecord[]> {
  const response = await api.get<HostListResponse>('/api/hosts');
  return response.data.hosts;
}

export async function getHost(hostId: number): Promise<HostRecord> {
  const response = await api.get<HostRecord>(`/api/hosts/${hostId}`);
  return response.data;
}

export async function updateHostAccess(
  hostId: number,
  payload: {
    human_access_url: string | null;
    human_access_instructions: string | null;
  },
): Promise<HostRecord> {
  const response = await api.patch<HostRecord>(
    `/api/hosts/${hostId}`,
    payload,
  );
  return response.data;
}


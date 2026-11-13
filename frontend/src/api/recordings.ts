import api from './axios';
import type {
  RecordingMode,
  RecordingPickResponse,
  RecordingSessionRecord,
} from '../types/workflow';

export interface StartRecordingPayload {
  mode: RecordingMode;
  name?: string;
  goal?: string;
  workflow_id?: number;
  start_url?: string;
}

export interface FinishRecordingPayload {
  name?: string;
  goal?: string;
  create_workflow?: boolean;
  workflow_id?: number;
}

export interface PickRecordingPayload {
  agent_id?: number;
  timeout?: number;
  x?: number;
  y?: number;
}

export async function startRecording(
  payload: StartRecordingPayload,
): Promise<RecordingSessionRecord> {
  const response = await api.post<RecordingSessionRecord>(
    '/api/recordings',
    payload,
  );
  return response.data;
}

export async function getRecording(
  recordingId: string,
): Promise<RecordingSessionRecord> {
  const response = await api.get<RecordingSessionRecord>(
    `/api/recordings/${recordingId}`,
  );
  return response.data;
}

export async function appendRecordingEvents(
  recordingId: string,
  events: Array<Record<string, unknown>>,
): Promise<RecordingSessionRecord> {
  const response = await api.post<RecordingSessionRecord>(
    `/api/recordings/${recordingId}/events`,
    { events },
  );
  return response.data;
}

export async function finishRecording(
  recordingId: string,
  payload: FinishRecordingPayload = {},
): Promise<RecordingSessionRecord> {
  const response = await api.post<RecordingSessionRecord>(
    `/api/recordings/${recordingId}/finish`,
    payload,
  );
  return response.data;
}

export async function pickRecordingElement(
  recordingId: string,
  payload: PickRecordingPayload = {},
): Promise<RecordingPickResponse> {
  const response = await api.post<RecordingPickResponse>(
    `/api/recordings/${recordingId}/pick`,
    payload,
  );
  return response.data;
}

export async function discardRecording(
  recordingId: string,
): Promise<{ status: string; recording_id: string }> {
  const response = await api.delete<{
    status: string;
    recording_id: string;
  }>(`/api/recordings/${recordingId}`);
  return response.data;
}

export function recordingApiBaseUrl(): string {
  return api.defaults.baseURL ?? 'http://localhost:8000';
}

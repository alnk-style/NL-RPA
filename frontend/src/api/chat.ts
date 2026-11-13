import api from './axios';
import type {
  ChatConfirmPayload,
  ChatMessagePayload,
  ChatModifyPayload,
  ChatResponse,
} from '../types/workflow';

export async function sendChatMessage(
  payload: ChatMessagePayload,
): Promise<ChatResponse> {
  const response = await api.post<ChatResponse>(
    '/api/chat/message',
    payload,
  );
  return response.data;
}

export async function modifyWorkflow(
  payload: ChatModifyPayload,
): Promise<ChatResponse> {
  const response = await api.post<ChatResponse>(
    '/api/chat/modify',
    payload,
  );
  return response.data;
}

export async function confirmWorkflow(
  payload: ChatConfirmPayload,
): Promise<{ status: string; workflow_id: number }> {
  const response = await api.post<{
    status: string;
    workflow_id: number;
  }>('/api/chat/confirm', payload);
  return response.data;
}

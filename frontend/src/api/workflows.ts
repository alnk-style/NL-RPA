import api from './axios';
import type {
  CreateWorkflowPayload,
  UpdateWorkflowPayload,
  WorkflowRecord,
} from '../types/workflow';

export async function listWorkflows(): Promise<WorkflowRecord[]> {
  const response = await api.get<WorkflowRecord[]>('/api/workflows');
  return response.data;
}

export async function getWorkflow(
  workflowId: number,
): Promise<WorkflowRecord> {
  const response = await api.get<WorkflowRecord>(
    `/api/workflows/${workflowId}`,
  );
  return response.data;
}

export async function createWorkflow(
  payload: CreateWorkflowPayload,
): Promise<WorkflowRecord> {
  const response = await api.post<WorkflowRecord>(
    '/api/workflows',
    payload,
  );
  return response.data;
}

export async function updateWorkflow(
  workflowId: number,
  payload: UpdateWorkflowPayload,
): Promise<WorkflowRecord> {
  const response = await api.put<WorkflowRecord>(
    `/api/workflows/${workflowId}`,
    payload,
  );
  return response.data;
}

export async function deleteWorkflow(
  workflowId: number,
): Promise<{ status: string; workflow_id: number }> {
  const response = await api.delete<{
    status: string;
    workflow_id: number;
  }>(`/api/workflows/${workflowId}`);
  return response.data;
}

export async function duplicateWorkflow(
  workflowId: number,
  newName: string,
): Promise<WorkflowRecord> {
  const response = await api.post<WorkflowRecord>(
    `/api/workflows/${workflowId}/duplicate`,
    { new_name: newName },
  );
  return response.data;
}

export async function publishWorkflow(
  workflowId: number,
): Promise<{ status: string; workflow_id: number }> {
  const response = await api.post<{
    status: string;
    workflow_id: number;
  }>(`/api/workflows/${workflowId}/publish`);
  return response.data;
}

export async function unpublishWorkflow(
  workflowId: number,
): Promise<{ status: string; workflow_id: number }> {
  const response = await api.post<{
    status: string;
    workflow_id: number;
  }>(`/api/workflows/${workflowId}/unpublish`);
  return response.data;
}

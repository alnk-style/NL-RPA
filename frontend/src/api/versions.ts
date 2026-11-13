import api from './axios';
import type {
  WorkflowVersionDiff,
  WorkflowVersionRecord,
} from '../types/workflow';

export async function listWorkflowVersions(
  workflowId: number,
): Promise<WorkflowVersionRecord[]> {
  const response = await api.get<{ items: WorkflowVersionRecord[] }>(
    `/api/workflows/${workflowId}/versions`,
  );
  return response.data.items;
}

export async function diffWorkflowVersions(
  workflowId: number,
  fromVersionId: number,
  toVersionId: number,
): Promise<WorkflowVersionDiff> {
  const response = await api.get<WorkflowVersionDiff>(
    `/api/workflows/${workflowId}/versions/diff`,
    {
      params: {
        from_version_id: fromVersionId,
        to_version_id: toVersionId,
      },
    },
  );
  return response.data;
}

export async function rollbackWorkflow(
  workflowId: number,
  versionId: number,
) {
  const response = await api.post<{
    status: string;
    workflow_id: number;
    active_version_id: number;
    version: number;
  }>(`/api/workflows/${workflowId}/rollback`, {
    version_id: versionId,
  });
  return response.data;
}

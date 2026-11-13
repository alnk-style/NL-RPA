import api from './axios';
import type {
  StructuredPlan,
  WorkflowDraftResult,
} from '../types/workflow';

export async function draftFromGoal(payload: {
  goal: string;
  start_url?: string | null;
  session_id?: string | null;
  workflow_id?: number | null;
  base_plan?: StructuredPlan | null;
  selected_step_id?: number | null;
}): Promise<WorkflowDraftResult> {
  const response = await api.post<WorkflowDraftResult>(
    '/api/workflows/draft-from-goal',
    payload,
  );
  return response.data;
}

export async function validateWorkflowDraft(
  workflowId: number,
  payload: {
    plan: StructuredPlan;
    draft_revision?: number | null;
  },
) {
  const response = await api.post<{
    valid: boolean;
    errors: string[];
    warnings: string[];
    action_catalog_version: string;
  }>(`/api/workflows/${workflowId}/validate`, payload);
  return response.data;
}

export async function observeWorkflow(
  workflowId: number,
  payload: {
    url: string;
    mode?: 'web';
    max_elements?: number;
    include_accessibility?: boolean;
  },
) {
  const response = await api.post<{
    workflow_id: number;
    observation: Record<string, unknown>;
    requirements: Record<string, unknown>;
  }>(`/api/workflows/${workflowId}/observe`, payload);
  return response.data;
}

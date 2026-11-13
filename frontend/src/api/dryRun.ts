import api from './axios';
import type {
  StructuredPlan,
  WorkflowDraftExecutionResult,
} from '../types/workflow';

export async function dryRunWorkflow(
  workflowId: number,
  payload: {
    plan: StructuredPlan;
    variables?: Record<string, unknown>;
    draft_revision?: number | null;
  },
): Promise<WorkflowDraftExecutionResult> {
  const response = await api.post<WorkflowDraftExecutionResult>(
    `/api/workflows/${workflowId}/dry-run`,
    payload,
  );
  return response.data;
}

export async function testWorkflowStep(
  workflowId: number,
  payload: {
    step_id: number;
    plan: StructuredPlan;
    variables?: Record<string, unknown>;
    draft_revision?: number | null;
  },
): Promise<WorkflowDraftExecutionResult> {
  const response = await api.post<WorkflowDraftExecutionResult>(
    `/api/workflows/${workflowId}/test-step`,
    payload,
  );
  return response.data;
}

import axios from 'axios';

import type {
  CreateWorkflowPayload,
  UpdateWorkflowPayload,
  WorkflowRecord,
} from '../types/workflow';

export interface WorkflowDraftApi {
  createWorkflow: (
    payload: CreateWorkflowPayload,
  ) => Promise<WorkflowRecord>;
  updateWorkflow: (
    workflowId: number,
    payload: UpdateWorkflowPayload,
  ) => Promise<WorkflowRecord>;
}

function isNotFoundError(error: unknown): boolean {
  return (
    axios.isAxiosError(error) &&
    error.response?.status === 404
  );
}

export async function saveWorkflowDraft(
  api: WorkflowDraftApi,
  options: {
    workflowId: number | null;
    createPayload: CreateWorkflowPayload;
    updatePayload: UpdateWorkflowPayload;
  },
): Promise<WorkflowRecord> {
  let workflowId = options.workflowId;

  if (workflowId === null) {
    const created = await api.createWorkflow(options.createPayload);
    workflowId = created.id;
  }

  try {
    return await api.updateWorkflow(workflowId, options.updatePayload);
  } catch (error) {
    if (!isNotFoundError(error)) {
      throw error;
    }
  }

  // A chat session can outlive a deleted workflow. Recreate the draft
  // instead of leaving the saved plan attached to a missing record.
  const recreated = await api.createWorkflow(options.createPayload);
  return api.updateWorkflow(recreated.id, options.updatePayload);
}

export default saveWorkflowDraft;

import api from './axios';
import type {
  ExecutionRecord,
  RunExecutionResult,
} from '../types/workflow';

export async function listExecutions(): Promise<ExecutionRecord[]> {
  const response = await api.get<ExecutionRecord[]>('/api/executions');
  return response.data;
}

export async function getExecutionDetail(
  executionId: number,
): Promise<ExecutionRecord> {
  const response = await api.get<ExecutionRecord>(
    `/api/executions/${executionId}`,
  );
  return response.data;
}

export async function runWorkflow(
  workflowId: number,
): Promise<RunExecutionResult> {
  const response = await api.post<RunExecutionResult>(
    `/api/executions/${workflowId}/run`,
  );
  return response.data;
}

export async function pauseExecution(
  executionId: number,
): Promise<ExecutionRecord> {
  const response = await api.post<ExecutionRecord>(
    `/api/executions/${executionId}/pause`,
  );
  return response.data;
}

export async function resumeExecution(
  executionId: number,
): Promise<ExecutionRecord> {
  const response = await api.post<ExecutionRecord>(
    `/api/executions/${executionId}/resume`,
  );
  return response.data;
}

export async function resumeExecutionAfterLogin(
  executionId: number,
): Promise<ExecutionRecord> {
  const response = await api.post<ExecutionRecord>(
    `/api/executions/${executionId}/resume-after-login`,
  );
  return response.data;
}

export async function cancelExecution(
  executionId: number,
  reason = '用户取消了执行。',
): Promise<ExecutionRecord> {
  const response = await api.post<ExecutionRecord>(
    `/api/executions/${executionId}/cancel`,
    { reason },
  );
  return response.data;
}

export async function getExecutionScreenshot(
  executionId: number,
  stepIndex: number,
): Promise<Blob> {
  const response = await api.get<Blob>(
    `/api/executions/${executionId}/screenshot/${stepIndex}`,
    { responseType: 'blob' },
  );
  return response.data;
}

export async function getExecutionArtifact(
  executionId: number,
  artifactIndex: number,
): Promise<Blob> {
  const response = await api.get<Blob>(
    `/api/executions/${executionId}/artifact/${artifactIndex}`,
    { responseType: 'blob' },
  );
  return response.data;
}

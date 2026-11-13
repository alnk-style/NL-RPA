import api from './axios';
import type {
  MetricsOverview,
  MetricsQueryParams,
} from '../types/metrics';

function toQuery(params: MetricsQueryParams): Record<string, string> {
  const query: Record<string, string> = {
    range: params.range ?? '7d',
  };
  if (params.workflowId != null) {
    query.workflow_id = String(params.workflowId);
  }
  if (params.executor) {
    query.executor = params.executor;
  }
  if (params.start) {
    query.start = params.start;
  }
  if (params.end) {
    query.end = params.end;
  }
  return query;
}

export async function getMetricsOverview(
  params: MetricsQueryParams = {},
): Promise<MetricsOverview> {
  const response = await api.get<MetricsOverview>(
    '/api/metrics/overview',
    { params: toQuery(params) },
  );
  return response.data;
}

export function isMetricsDisabledError(error: unknown): boolean {
  const status = (
    error as { response?: { status?: number } } | undefined
  )?.response?.status;
  return status === 403;
}

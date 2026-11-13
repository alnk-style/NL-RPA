import axios from 'axios';

interface ApiErrorPayload {
  detail?:
    | string
    | {
        code?: string;
        reason?: string;
        message?: string;
      };
  message?: string;
  error?: {
    code?: string;
    message?: string;
  };
}

export function getApiErrorMessage(
  error: unknown,
  fallback: string,
): string {
  if (!axios.isAxiosError<ApiErrorPayload>(error)) {
    return error instanceof Error && error.message
      ? error.message
      : fallback;
  }

  const payload = error.response?.data;
  const detail = payload?.detail;
  if (typeof detail === 'string' && detail) {
    return detail;
  }
  if (detail && typeof detail === 'object') {
    const structured = detail.reason || detail.message;
    if (typeof structured === 'string' && structured) {
      return structured;
    }
  }
  return (
    payload?.error?.message ||
    payload?.message ||
    error.message ||
    fallback
  );
}

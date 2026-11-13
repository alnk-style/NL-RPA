const SENSITIVE_ASSIGNMENT = /\b(token|access_token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s&,;]+/gi;
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;

export function redactSensitiveText(
  value: unknown,
  fallback = '—',
): string {
  const text = String(value ?? '').trim();
  if (!text) {
    return fallback;
  }
  return text
    .replace(BEARER_TOKEN, 'Bearer ***')
    .replace(SENSITIVE_ASSIGNMENT, (_match, name: string) => (
      `${name}=***`
    ));
}


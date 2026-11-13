import type { WorkflowStep } from '../types/workflow';

export type MCPControlType =
  | 'text'
  | 'number'
  | 'boolean'
  | 'enum'
  | 'string_list'
  | 'json';

export interface MCPFormField {
  key: string;
  label: string;
  control: MCPControlType;
  required: boolean;
  description: string;
  options: string[];
  defaultValue?: unknown;
}

export interface MCPDraftConfig {
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  resultVar: string;
  resultPath: string;
  confirm: boolean;
  timeoutSeconds: number | null;
  retryAttempts: number;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function schemaFieldControl(schema: Record<string, unknown>): MCPControlType {
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return 'enum';
  }
  if (schema.type === 'boolean') {
    return 'boolean';
  }
  if (schema.type === 'integer' || schema.type === 'number') {
    return 'number';
  }
  if (
    schema.type === 'array' &&
    record(schema.items).type === 'string'
  ) {
    return 'string_list';
  }
  if (schema.type === 'string') {
    return 'text';
  }
  return 'json';
}

export function schemaToFields(
  inputSchema: Record<string, unknown> | undefined,
): MCPFormField[] {
  const schema = record(inputSchema);
  const properties = record(schema.properties);
  const required = Array.isArray(schema.required)
    ? schema.required.map(String)
    : [];
  return Object.entries(properties).map(([key, raw]) => {
    const item = record(raw);
    return {
      key,
      label: text(item.title) || key,
      control: schemaFieldControl(item),
      required: required.includes(key),
      description:
        text(item.description) || text(item.title) || key,
      options: Array.isArray(item.enum)
        ? item.enum.map(String)
        : [],
      defaultValue: item.default,
    };
  });
}

export function buildMCPArguments(
  fields: MCPFormField[],
  current: Record<string, unknown> = {},
): Record<string, unknown> {
  const next: Record<string, unknown> = {};
  fields.forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(current, field.key)) {
      next[field.key] = current[field.key];
    } else if (field.defaultValue !== undefined) {
      next[field.key] = field.defaultValue;
    }
  });
  Object.entries(current).forEach(([key, value]) => {
    if (!(key in next) && !fields.some((field) => field.key === key)) {
      next[key] = value;
    }
  });
  return next;
}

export function buildMCPStep(input: {
  stepId?: string | number;
  server: string;
  tool: string;
  arguments?: Record<string, unknown>;
  resultVar?: string;
  resultPath?: string;
  confirm?: boolean;
  timeoutSeconds?: number | null;
  retryAttempts?: number;
  dependsOn?: Array<string | number>;
}): WorkflowStep {
  return {
    step_id: input.stepId,
    executor: 'mcp',
    action: 'call_tool',
    params: {
      server: input.server,
      tool: input.tool,
      arguments: input.arguments ?? {},
      result_var: input.resultVar ?? '',
      result_path: input.resultPath ?? '',
      confirm: Boolean(input.confirm),
    },
    timeout_seconds: input.timeoutSeconds ?? undefined,
    retry_policy: {
      max_attempts: Math.max(1, input.retryAttempts ?? 2),
      backoff_seconds: 1,
    },
    depends_on: input.dependsOn ?? [],
  };
}

export function parseMCPStep(step: WorkflowStep | undefined): MCPDraftConfig {
  const params = record(step?.params);
  const retry = record(step?.retry_policy);
  const timeout = Number(step?.timeout_seconds);
  const attempts = Number(retry.max_attempts);
  return {
    server: text(params.server),
    tool: text(params.tool),
    arguments: record(params.arguments),
    resultVar: text(params.result_var),
    resultPath: text(params.result_path),
    confirm: params.confirm === true,
    timeoutSeconds:
      Number.isFinite(timeout) && timeout > 0 ? timeout : null,
    retryAttempts:
      Number.isFinite(attempts) && attempts >= 1
        ? Math.trunc(attempts)
        : 2,
  };
}

export function formatMCPResultPath(value: unknown): string {
  const path = text(value);
  if (!path) {
    return '未设置';
  }
  return path.length > 72 ? `${path.slice(0, 71)}…` : path;
}


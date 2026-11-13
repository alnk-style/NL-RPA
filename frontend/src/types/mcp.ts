export type MCPRiskLevel = 'read_only' | 'mutating' | 'destructive';
export type MCPTransport = 'http' | 'stdio';
export type MCPSettingSource = 'runtime' | 'environment';

export interface MCPSettingsRecord {
  enabled: boolean;
  source: MCPSettingSource;
  env_default: boolean;
  sdk_available: boolean;
  stdio_enabled: boolean;
}

export interface MCPToolRecord {
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
  annotations?: Record<string, unknown>;
  risk_level: MCPRiskLevel;
  authorized: boolean;
  confirmed: boolean;
  available?: boolean;
  authorized_at?: string | null;
  authorized_by?: string | null;
  last_seen_at?: string | null;
}

export interface MCPServerRecord {
  id: number;
  name: string;
  description: string | null;
  transport: MCPTransport;
  url: string | null;
  command: string | null;
  args: string[];
  env_keys: string[];
  headers: Record<string, unknown>;
  enabled: boolean;
  default_risk: MCPRiskLevel;
  timeout_seconds: number | null;
  has_secret: boolean;
  version?: string | null;
  fingerprint?: string | null;
  signature?: string | null;
  toolset_fingerprint?: string | null;
  workspace_id?: number | null;
  local_app_id?: string | null;
  local_managed?: boolean;
  tools: MCPToolRecord[];
  last_error: string | null;
  last_tested_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MCPToolCatalogItem {
  server_id: number;
  server: string;
  app_name: string;
  tool: string;
  description: string;
  input_schema: Record<string, unknown>;
  risk_level: MCPRiskLevel;
  authorized: boolean;
  confirmed: boolean;
  metadata_trust?: 'untrusted';
  metadata_kind?: string;
}

export interface MCPToolCatalogResponse {
  enabled: boolean;
  tools: MCPToolCatalogItem[];
  metadata_trust?: 'untrusted';
}

export interface MCPServerListResponse {
  enabled: boolean;
  sdk_available: boolean;
  servers: MCPServerRecord[];
}

export interface MCPServerCreateInput {
  name: string;
  description?: string | null;
  transport: MCPTransport;
  url?: string | null;
  command?: string | null;
  args?: string[];
  headers?: Record<string, string>;
  auth_token?: string | null;
  enabled?: boolean;
  default_risk?: MCPRiskLevel;
  timeout_seconds?: number | null;
}

export interface MCPServerTestResult {
  success: boolean;
  server_id?: number;
  server?: string;
  transport?: string;
  tool_count?: number;
  tools?: MCPToolRecord[];
  duration_ms?: number;
  error?: string;
  failure_category?: string;
}

export interface MCPToolAuthorizeResult {
  success: boolean;
  server_id?: number;
  tool?: MCPToolRecord;
  error?: string;
}

export interface MCPToolCallAudit {
  id: number;
  server_id: number | null;
  server: string;
  tool: string;
  risk_level: MCPRiskLevel;
  decision: string;
  status: string;
  arguments: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: string | null;
  duration_ms: number | null;
  attempts: number;
  workflow_id: number | null;
  execution_id: number | null;
  user_id: number | null;
  created_at: string | null;
}

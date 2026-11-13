export type LocalAppState =
  | 'discovered'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'error'
  | 'unsupported';
export type LocalAppHostMode = 'subprocess' | 'in_process';
export type LocalAppTransportKind = 'process' | 'in_process';

export interface LocalAppInfo {
  app_id: string;
  name: string;
  description: string;
  version: string;
  platform: string;
  tools_count: number;
  state: LocalAppState;
  url: string | null;
  pid: number | null;
  port: number | null;
  started_at: string | null;
  last_health_at: string | null;
  last_error: string | null;
  auto_start: boolean;
  server_id: number | null;
  discovered_tool_count: number | null;
  unsupported_reason: string | null;
  host_mode: LocalAppHostMode;
  transport_kind: LocalAppTransportKind;
  generated: boolean;
}

export interface LocalAppBinding {
  app_id: string;
  bound: boolean;
  is_owner: boolean;
  can_manage: boolean;
  auto_start: boolean;
  workspace_bound: boolean;
  host_mode: LocalAppHostMode;
  owner_user_id?: number;
  workspace_id?: number | null;
}

export interface LocalAppScanError {
  app_id: string;
  error: string;
}

export interface LocalAppListResponse {
  apps: LocalAppInfo[];
  errors: LocalAppScanError[];
}

export interface LocalAppScanResponse extends LocalAppListResponse {
  added: string[];
  removed: string[];
}

export interface LocalAppOperationResponse {
  app: LocalAppInfo;
  server_id: number | null;
  discovered_tool_count: number | null;
}

export interface LocalAppStopResponse {
  stopped: boolean;
  app: LocalAppInfo;
}

export interface LocalAppLogsResponse {
  app_id: string;
  lines: number;
  logs: string;
}

export interface GeneratedInstalledAppResult {
  app_id: string;
  app_dir: string;
  executable_path: string;
  template: string;
}

export interface GeneratedInstalledAppResponse
  extends LocalAppOperationResponse {
  generated: GeneratedInstalledAppResult;
}

export type HostEnvironment = 'local' | 'vm' | 'unknown';
export type HostStatus = 'online' | 'busy' | 'offline';

export interface HostHumanAccessInfo {
  url?: string | null;
  instructions?: string | null;
}

export interface HostRecord {
  host_id: number;
  name: string;
  environment: HostEnvironment;
  capabilities: string[];
  status: HostStatus;
  online: boolean;
  last_seen_at: string | null;
  agent_version: string;
  platform: string;
  active_task_count: number;
  session_ready: boolean;
  human_access_info: HostHumanAccessInfo;
  connection_security: string;
}

export interface HostListResponse {
  hosts: HostRecord[];
}


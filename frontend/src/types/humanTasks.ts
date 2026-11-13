export interface HumanTaskRecord {
  id: number;
  execution_id: number;
  step_id: number | null;
  kind: string;
  instruction: string;
  payload: Record<string, unknown>;
  screenshot_path: string | null;
  status: string;
  created_at: string;
  resolved_at: string | null;
  expires_at: string | null;
  workflow_name?: string | null;
  workflow_version?: number | null;
  execution_status?: string | null;
  host_id?: number | null;
  host_environment?: string | null;
  host_bound?: boolean;
  host_affinity_configured?: boolean;
  waiting_seconds?: number;
  can_resume?: boolean;
  current_url?: string | null;
  browser_profile_id?: number | null;
  last_verification_error?: string | null;
}

export interface HumanTaskListResponse {
  items: HumanTaskRecord[];
}

export interface HumanTaskResumeResponse {
  human_task: HumanTaskRecord;
  execution: {
    id: number;
    status: string;
    human_task_id: number | null;
  };
  idempotent: boolean;
}

export interface BrowserLoginSessionResponse {
  human_task_id: number;
  browser_profile_id: number;
  profile_name: string;
  status: string;
  current_url: string | null;
}

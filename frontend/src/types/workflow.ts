export type WorkflowStatus = 'draft' | 'published' | 'disabled';
export type TriggerType = 'manual' | 'cron';
export type ExecutorType = 'web' | 'windows' | 'mcp' | 'hybrid';
export type StepExecutorType =
  | 'web'
  | 'transform'
  | 'files'
  | 'email'
  | 'windows'
  | 'mcp'
  | 'code';
export type RecordingMode = 'web' | 'windows';
export type HostRequirement =
  | 'any'
  | 'headless_web'
  | 'interactive_desktop'
  | 'isolated_desktop';

export interface WorkflowTrigger {
  type?: TriggerType | string;
  cron?: string | null;
  [key: string]: unknown;
}

export interface WorkflowStep {
  step_id?: string | number;
  executor?: StepExecutorType;
  action?: string;
  target?: string;
  selector?: string;
  params?: Record<string, unknown>;
  locator?: Record<string, unknown> | null;
  retry_policy?: Record<string, unknown>;
  timeout_seconds?: number;
  on_failure?: string;
  idempotency_key?: string | null;
  host_requirement?: HostRequirement;
  condition?: unknown;
  enabled?: boolean;
  depends_on?: Array<string | number>;
  [key: string]: unknown;
}

export interface StructuredPlan {
  trigger?: WorkflowTrigger;
  steps?: WorkflowStep[];
  variables?: unknown[];
  executor_type?: ExecutorType;
  required_capabilities?: string[];
  [key: string]: unknown;
}

export interface WorkflowRecord {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  natural_language_input: string;
  structured_plan: StructuredPlan | null;
  generated_code: string | null;
  dag_definition: Record<string, unknown> | null;
  trigger_type: TriggerType;
  cron_expression: string | null;
  status: WorkflowStatus;
  notification_config: Record<string, unknown> | null;
  executor_type: ExecutorType;
  required_capabilities: string[] | null;
  execution_mode: 'structured' | 'legacy_code' | string;
  execution_policy: Record<string, unknown>;
  active_version_id: number | null;
  schedule_timezone: string;
  draft_revision: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowVersionRecord {
  id: number;
  workflow_id: number;
  version: number;
  plan: StructuredPlan;
  validation: Record<string, unknown>;
  action_catalog_version: string;
  created_at: string;
}

export interface WorkflowVersionChange {
  kind: 'add' | 'remove' | 'change' | string;
  path: string;
  before?: unknown;
  after?: unknown;
}

export interface WorkflowVersionDiff {
  from_version_id: number;
  to_version_id: number;
  changes: WorkflowVersionChange[];
}

export interface WorkflowDraftResult {
  plan: StructuredPlan;
  base_plan: StructuredPlan | null;
  valid: boolean;
  errors: string[];
  warnings: string[];
  needs_confirmation: boolean;
  observations: Record<string, unknown> | null;
  changes: WorkflowVersionChange[];
  requirements: Record<string, unknown>;
}

export interface WorkflowDraftExecutionResult {
  workflow_id: number;
  draft_revision: number;
  mode: 'test' | 'dry_run';
  formal_execution: false;
  success: boolean;
  status: string;
  message: string;
  error: string | null;
  duration_seconds: number;
  variables: Record<string, unknown>;
  artifacts: ExecutionArtifact[];
  step_logs: unknown[];
  requirements: Record<string, unknown>;
}

export interface ChatResponse {
  reply: string;
  structured_plan: StructuredPlan | null;
  generated_code: string | null;
  needs_confirmation: boolean;
  suggestions: string[];
  executor_type: ExecutorType;
}

export interface ChatMessagePayload {
  session_id: string;
  message: string;
  workflow_id?: number | null;
}

export interface ChatModifyPayload {
  session_id: string;
  modification: string;
  workflow_id: number;
}

export interface ChatConfirmPayload {
  session_id: string;
  workflow_id: number;
}

export interface CreateWorkflowPayload {
  name: string;
  description?: string | null;
  natural_language_input: string;
  executor_type?: ExecutorType;
  required_capabilities?: string[];
}

export interface UpdateWorkflowPayload {
  name?: string;
  description?: string | null;
  natural_language_input?: string;
  structured_plan?: StructuredPlan | null;
  generated_code?: string | null;
  dag_definition?: Record<string, unknown> | null;
  trigger_type?: TriggerType;
  cron_expression?: string | null;
  status?: WorkflowStatus;
  notification_config?: Record<string, unknown> | null;
  executor_type?: ExecutorType;
  required_capabilities?: string[] | null;
  execution_policy?: Record<string, unknown> | null;
  draft_revision?: number;
}

export interface ExecutionRecord {
  id: number;
  workflow_id: number;
  workflow_name?: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  screenshots: Array<string | null> | null;
  artifacts: ExecutionArtifact[] | null;
  step_logs: unknown[] | null;
  workflow_version_id?: number | null;
  workflow_version?: number | null;
  variables?: Record<string, unknown>;
  deliveries?: ExecutionDeliveryRecord[];
  execution_policy_snapshot?: Record<string, unknown> | null;
  host_id?: number | null;
  host_environment?: string | null;
  host_bound?: boolean;
  host_affinity_configured?: boolean;
  host_name?: string | null;
  host_status?: string | null;
  host_capabilities?: string[];
  host_reason?: string | null;
  human_task_id?: number | null;
  trigger_type?: string;
}

export interface ExecutionDeliveryRecord {
  id: number;
  execution_id: number;
  step_id?: number | null;
  channel: string;
  status: string;
  recipient?: unknown;
  attachment_paths?: unknown[] | null;
  provider_message_id?: string | null;
  error?: string | null;
  created_at: string;
  finished_at?: string | null;
}

export interface ExecutionArtifact {
  name?: string;
  path?: string | null;
  requested_path?: string | null;
  delivered?: boolean;
  delivered_path?: string | null;
  size?: number | null;
  media_type?: string | null;
  summary?: string | null;
  source?: {
    server?: string;
    tool?: string;
    [key: string]: unknown;
  } | null;
  error?: string | null;
  [key: string]: unknown;
}

export type ExecutionStepStatus =
  | 'success'
  | 'warning'
  | 'failed'
  | 'running'
  | 'pending'
  | 'skipped'
  | 'awaiting_user'
  | 'cancelled'
  | 'unknown';

export interface ExecutionStepLog {
  index: number;
  action: string;
  target: string;
  selector: string | null;
  status: ExecutionStepStatus;
  durationSeconds: number | null;
  screenshotPath: string | null;
  hasScreenshot: boolean;
  error: string | null;
  screenshotError: string | null;
  startedAt: number | null;
  finishedAt: number | null;
  executor?: string;
  server?: string;
  tool?: string;
  risk_level?: string;
  decision?: string;
  duration_ms?: number;
  attempts?: number;
  audit_id?: number;
  artifact_count?: number;
  result_path?: string;
  arguments_summary?: Record<string, unknown> | null;
  result_summary?: unknown;
}

export interface RunExecutionResult extends ExecutionRecord {
  execution_id: number;
  success?: boolean;
  output?: string;
  error?: string | null;
  duration_seconds?: number;
}

export interface WorkflowGraphNode {
  id: string;
  kind: 'trigger' | 'step';
  label: string;
  detail?: string;
  stepIndex: number;
}

export interface WorkflowGraphEdge {
  id: string;
  source: string;
  target: string;
}

export interface WorkflowGraph {
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}

export interface RecordingDraftStep {
  step_id?: string | number;
  executor?: StepExecutorType | string;
  action?: string;
  target?: string;
  detail?: string;
  locator?: Record<string, unknown> | string | null;
  [key: string]: unknown;
}

export interface RecordingDraft {
  plan: StructuredPlan;
  warnings: string[];
  event_count: number;
  dropped_count: number;
  steps: RecordingDraftStep[];
}

export interface RecordingSessionRecord {
  id: string;
  mode: string;
  status: string;
  name?: string | null;
  goal?: string | null;
  event_count: number;
  workflow_id?: number | null;
  recording_path?: string | null;
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
  draft?: RecordingDraft | null;
}

export interface RecordingPickResponse extends RecordingSessionRecord {
  picked?: {
    element?: Record<string, unknown> | null;
    window?: Record<string, unknown> | null;
    [key: string]: unknown;
  };
}

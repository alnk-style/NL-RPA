export interface EmailSettingsRecord {
  host: string;
  port: number;
  username: string;
  from_email: string;
  start_tls: boolean;
  default_recipient: string;
  credential_key: string;
  password_configured: boolean;
}

export interface EmailDeliveryResult {
  success: boolean;
  status: string;
  message?: string;
  error_category?: string | null;
  error?: string | null;
  message_id?: string | null;
  delivery_record_id?: number | null;
}

export interface BrowserProfileRecord {
  id: number;
  user_id: number;
  name: string;
  user_data_dir: string | null;
  profile_directory: string;
  login_state: string;
  last_verified_at: string | null;
  created_at: string;
}

export interface ExportDirectorySettings {
  directories: string[];
}

export type VisualProviderName =
  | 'disabled'
  | 'local_ocr'
  | 'remote_vlm';

export interface VisualSettingsRecord {
  enabled: boolean;
  provider: VisualProviderName;
  endpoint: string;
  model: string;
  confidence_threshold: number;
  max_candidates: number;
  max_attempts: number;
  screenshot_scope: 'viewport' | 'window';
  redaction_policy: Record<string, unknown>;
  api_key_configured: boolean;
}

export interface VisualProviderTestResult {
  status: string;
  provider: string;
  model?: string;
  candidate_count?: number;
  message?: string;
}

export interface WorkflowCredentialKeys {
  workflow_id: number;
  keys: string[];
}

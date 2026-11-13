export interface WorkflowModeInfo {
  mode: 'structured' | 'legacy_code' | 'unknown';
  label: string;
  versionLabel: string;
  readOnly: boolean;
  warning: boolean;
}

export function workflowModeLabel(value: {
  execution_mode?: string | null;
  active_version_id?: number | null;
}): WorkflowModeInfo {
  const mode = value.execution_mode;
  const versionLabel = value.active_version_id
    ? `v${value.active_version_id}`
    : '未发布';
  if (mode === 'structured') {
    return {
      mode,
      label: '结构化',
      versionLabel,
      readOnly: false,
      warning: false,
    };
  }
  if (mode === 'legacy_code') {
    return {
      mode,
      label: '旧代码模式',
      versionLabel,
      readOnly: true,
      warning: false,
    };
  }
  return {
    mode: 'unknown',
    label: '未知模式',
    versionLabel,
    readOnly: true,
    warning: true,
  };
}

export function executionVersionLabel(value: {
  workflow_version_id?: number | null;
}): string {
  return value.workflow_version_id
    ? `v${value.workflow_version_id}`
    : '无版本';
}

import type {
  MCPRiskLevel,
  MCPToolCallAudit,
  MCPToolRecord,
} from '../types/mcp';

export function mcpSettingsStatusLabel(enabled: boolean): string {
  return enabled ? '已开启' : '已关闭';
}

export function mcpSettingSourceLabel(source: string): string {
  if (source === 'runtime') {
    return '应用内设置';
  }
  if (source === 'environment') {
    return '环境默认值';
  }
  return source;
}

export interface ToolSummary {
  total: number;
  readOnly: number;
  mutating: number;
  destructive: number;
  authorized: number;
  pendingAuthorization: number;
  missingConfirmation: number;
}

export function riskLabel(level: MCPRiskLevel | string): string {
  if (level === 'read_only') {
    return '只读';
  }
  if (level === 'mutating') {
    return '写入';
  }
  if (level === 'destructive') {
    return '破坏性';
  }
  return String(level);
}

export function riskVariant(
  level: MCPRiskLevel | string,
): 'success' | 'warning' | 'danger' | 'secondary' {
  if (level === 'read_only') {
    return 'success';
  }
  if (level === 'destructive') {
    return 'danger';
  }
  if (level === 'mutating') {
    return 'warning';
  }
  return 'secondary';
}

export function toolAuthorizationLabel(tool: MCPToolRecord): string {
  if (tool.risk_level === 'read_only') {
    return '默认允许';
  }
  if (!tool.authorized) {
    return '未授权';
  }
  if (tool.risk_level === 'destructive' && !tool.confirmed) {
    return '已授权，待确认';
  }
  return '已授权';
}

export function toolNeedsConfirmation(tool: MCPToolRecord): boolean {
  return tool.risk_level === 'destructive';
}

export function summarizeTools(
  tools: MCPToolRecord[] | undefined,
): ToolSummary {
  const list = Array.isArray(tools) ? tools : [];
  const summary: ToolSummary = {
    total: list.length,
    readOnly: 0,
    mutating: 0,
    destructive: 0,
    authorized: 0,
    pendingAuthorization: 0,
    missingConfirmation: 0,
  };
  list.forEach((tool) => {
    if (tool.risk_level === 'read_only') {
      summary.readOnly += 1;
    } else if (tool.risk_level === 'mutating') {
      summary.mutating += 1;
    } else if (tool.risk_level === 'destructive') {
      summary.destructive += 1;
    }
    if (tool.authorized) {
      summary.authorized += 1;
    } else if (tool.risk_level !== 'read_only') {
      summary.pendingAuthorization += 1;
    }
    if (
      tool.risk_level === 'destructive' &&
      tool.authorized &&
      !tool.confirmed
    ) {
      summary.missingConfirmation += 1;
    }
  });
  return summary;
}

export function auditStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    success: '成功',
    failed: '失败',
    timeout: '超时',
    denied: '已拒绝',
    error: '错误',
  };
  return labels[status] ?? status;
}

export function auditStatusVariant(
  status: string,
): 'success' | 'danger' | 'warning' | 'secondary' {
  if (status === 'success') {
    return 'success';
  }
  if (status === 'denied' || status === 'timeout') {
    return 'warning';
  }
  if (status === 'failed' || status === 'error') {
    return 'danger';
  }
  return 'secondary';
}

export function formatDurationMs(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '—';
  }
  if (value < 1000) {
    return `${Math.round(value)}ms`;
  }
  return `${(value / 1000).toFixed(2)}s`;
}

export function formatAuditTarget(audit: MCPToolCallAudit): string {
  const parts: string[] = [audit.server, audit.tool];
  if (typeof audit.execution_id === 'number') {
    parts.push(`执行 #${audit.execution_id}`);
  }
  return parts.join(' · ');
}

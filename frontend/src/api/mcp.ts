import api from './axios';
import type {
  MCPServerCreateInput,
  MCPServerListResponse,
  MCPServerRecord,
  MCPServerTestResult,
  MCPSettingsRecord,
  MCPToolAuthorizeResult,
  MCPToolCallAudit,
  MCPToolCatalogResponse,
} from '../types/mcp';

export async function getMCPSettings(): Promise<MCPSettingsRecord> {
  const response = await api.get<MCPSettingsRecord>('/api/mcp/settings');
  return response.data;
}

export async function updateMCPSettings(payload: {
  enabled: boolean;
}): Promise<MCPSettingsRecord> {
  const response = await api.patch<MCPSettingsRecord>(
    '/api/mcp/settings',
    payload,
  );
  return response.data;
}

export async function listMCPServers(): Promise<MCPServerListResponse> {
  const response = await api.get<MCPServerListResponse>('/api/mcp/servers');
  return response.data;
}

export async function createMCPServer(
  payload: MCPServerCreateInput,
): Promise<MCPServerRecord> {
  const response = await api.post<{ server: MCPServerRecord }>(
    '/api/mcp/servers',
    payload,
  );
  return response.data.server;
}

export async function updateMCPServer(
  serverId: number,
  payload: Partial<MCPServerCreateInput>,
): Promise<MCPServerRecord> {
  const response = await api.patch<{ server: MCPServerRecord }>(
    `/api/mcp/servers/${serverId}`,
    payload,
  );
  return response.data.server;
}

export async function deleteMCPServer(serverId: number): Promise<void> {
  await api.delete(`/api/mcp/servers/${serverId}`);
}

export async function testMCPServer(
  serverId: number,
): Promise<MCPServerTestResult> {
  const response = await api.post<MCPServerTestResult>(
    `/api/mcp/servers/${serverId}/test`,
  );
  return response.data;
}

export async function authorizeMCPTool(
  serverId: number,
  tool: string,
  payload: {
    authorized: boolean;
    confirmed: boolean;
    risk_level?: string;
  },
): Promise<MCPToolAuthorizeResult> {
  const response = await api.post<MCPToolAuthorizeResult>(
    `/api/mcp/servers/${serverId}/tools/${encodeURIComponent(tool)}/authorize`,
    payload,
  );
  return response.data;
}

export async function listMCPAudits(
  limit = 50,
): Promise<MCPToolCallAudit[]> {
  const response = await api.get<{ audits: MCPToolCallAudit[] }>(
    '/api/mcp/audits',
    { params: { limit } },
  );
  return response.data.audits;
}

export async function listMCPCatalog(
  query?: string,
  limit = 50,
): Promise<MCPToolCatalogResponse> {
  const response = await api.get<MCPToolCatalogResponse>(
    '/api/mcp/catalog',
    {
      params: {
        q: query?.trim() || undefined,
        limit,
      },
    },
  );
  return response.data;
}

export async function listMCPAuditsByServer(
  serverId: number,
  limit = 50,
): Promise<MCPToolCallAudit[]> {
  const response = await api.get<{ audits: MCPToolCallAudit[] }>(
    '/api/mcp/audits',
    {
      params: {
        server_id: serverId,
        limit,
      },
    },
  );
  return response.data.audits;
}

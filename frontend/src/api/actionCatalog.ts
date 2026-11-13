import api from './axios';
import type { ActionCatalog } from '../lib/action-catalog';

export async function getActionCatalog(): Promise<ActionCatalog> {
  const response = await api.get<ActionCatalog>('/api/action-catalog');
  return response.data;
}

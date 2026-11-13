import api from './axios';
import type { User } from '../store/useStore';

export async function getCurrentUser(): Promise<User> {
  const response = await api.get<User>('/api/auth/me');
  return response.data;
}

import api from './axios';
import type {
  EmailDeliveryResult,
  EmailSettingsRecord,
} from '../types/connections';
import type { EmailSettingsUpdate } from '../lib/connections';

export async function getEmailSettings(): Promise<EmailSettingsRecord> {
  const response = await api.get<EmailSettingsRecord>(
    '/api/settings/email',
  );
  return response.data;
}

export async function updateEmailSettings(
  payload: EmailSettingsUpdate,
): Promise<EmailSettingsRecord> {
  const response = await api.put<EmailSettingsRecord>(
    '/api/settings/email',
    payload,
  );
  return response.data;
}

export async function testEmailConnection(): Promise<EmailDeliveryResult> {
  const response = await api.post<EmailDeliveryResult>(
    '/api/settings/email/test',
  );
  return response.data;
}

export async function sendTestEmail(
  recipient?: string,
): Promise<EmailDeliveryResult> {
  const response = await api.post<EmailDeliveryResult>(
    '/api/settings/email/test-send',
    { recipient: recipient || null },
  );
  return response.data;
}

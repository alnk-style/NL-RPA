export interface EmailSettingsFormValue {
  host: string;
  port: number;
  username: string;
  password: string;
  from_email: string;
  start_tls: boolean;
  default_recipient: string;
  credential_key: string;
}

export type EmailSettingsUpdate = Omit<
  EmailSettingsFormValue,
  'password'
> & {
  password?: string;
};

export function buildEmailSettingsUpdate(
  form: EmailSettingsFormValue,
): EmailSettingsUpdate {
  const { password, ...visible } = form;
  return password.trim()
    ? { ...visible, password }
    : visible;
}

export function parseExportDirectoryLines(value: string): string[] {
  const seen = new Set<string>();
  const directories: string[] = [];
  for (const raw of value.split(/[\n;]+/)) {
    const text = raw.trim();
    if (!text) {
      continue;
    }
    const absolute = (
      /^[A-Za-z]:[\\/]/.test(text)
      || text.startsWith('\\\\')
      || text.startsWith('/')
    );
    if (!absolute || seen.has(text)) {
      continue;
    }
    seen.add(text);
    directories.push(text);
  }
  return directories;
}

export function sanitizeCredentialKeys(keys: unknown): string[] {
  if (!Array.isArray(keys)) {
    return [];
  }
  return Array.from(
    new Set(
      keys
        .map((key) => String(key).trim())
        .filter(Boolean),
    ),
  ).sort();
}

export default buildEmailSettingsUpdate;

export interface DiscoverableInstalledApp {
  key: string;
  name: string;
  executable_path: string;
  source: string;
  icon_url: string;
  generated_app_id: string | null;
}

export interface DiscoverableInstalledAppsResponse {
  apps: DiscoverableInstalledApp[];
  supported: boolean;
}

export const SCOPED_DESKTOP_TOOLS = [
  { name: 'app_launch', risk: 'mutating' },
  { name: 'app_observe', risk: 'read_only' },
  { name: 'app_activate', risk: 'mutating' },
  { name: 'app_read_text', risk: 'read_only' },
] as const;

export function filterDiscoverableApps(
  apps: DiscoverableInstalledApp[],
  query: string,
): DiscoverableInstalledApp[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) {
    return apps;
  }
  return apps.filter(
    (app) =>
      app.name.toLocaleLowerCase().includes(needle) ||
      app.executable_path.toLocaleLowerCase().includes(needle),
  );
}

export function installedAppIconUrl(
  iconUrl: string,
  backendUrl: string,
): string {
  if (/^https?:\/\//i.test(iconUrl)) {
    return iconUrl;
  }
  return `${backendUrl.replace(/\/+$/, '')}/${iconUrl.replace(/^\/+/, '')}`;
}

export function executableLabel(executablePath: string): string {
  const parts = executablePath.split(/[\\/]/);
  return parts[parts.length - 1] || executablePath;
}

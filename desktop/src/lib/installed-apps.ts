export interface InstalledApp {
  key: string;
  name: string;
  executable_path: string;
  source: string;
  icon_url: string;
}

export interface InstalledAppsResponse {
  apps: InstalledApp[];
  supported: boolean;
}

export function filterInstalledApps(
  apps: InstalledApp[],
  query: string,
): InstalledApp[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) {
    return apps;
  }
  return apps.filter((app) => {
    return (
      app.name.toLocaleLowerCase().includes(needle) ||
      app.executable_path.toLocaleLowerCase().includes(needle)
    );
  });
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

export function pathKey(value: string): string {
  return value.trim().replace(/\//g, '\\').toLocaleLowerCase();
}

export function installedAppForPath(
  apps: InstalledApp[],
  executablePath: string,
): InstalledApp | undefined {
  const needle = pathKey(executablePath);
  return apps.find((app) => pathKey(app.executable_path) === needle);
}

export function executableLabel(executablePath: string): string {
  const parts = executablePath.split(/[\\/]/);
  return parts[parts.length - 1] || executablePath;
}

export const LOCAL_BACKEND_URL = 'http://localhost:8000';
export const REMOTE_BACKEND_URL_KEY = 'nl-rpa.remoteBackendUrl';
export const RUN_MODE_KEY = 'nl-rpa.runMode';

export type RunMode = 'local' | 'remote';

function trimTrailingSlash(value: string): string {
  return value.trim().replace(/\/+$/, '');
}

function readConfiguredRemoteUrl(): string | null {
  if (typeof localStorage !== 'undefined') {
    const storedUrl = readStoredValue(REMOTE_BACKEND_URL_KEY)
      ?? readStoredValue('backendUrl')
      ?? readStoredValue('remoteBackendUrl');

    if (storedUrl?.trim()) {
      return trimTrailingSlash(storedUrl);
    }
  }

  const environmentUrl =
    import.meta.env.VITE_REMOTE_BACKEND_URL ??
    import.meta.env.VITE_API_BASE_URL;

  if (environmentUrl?.trim()) {
    return trimTrailingSlash(environmentUrl);
  }

  return null;
}

function readStoredValue(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function isTauriMode(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }

  const tauriWindow = window as Window & {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  };

  return Boolean(
    tauriWindow.__TAURI__ ?? tauriWindow.__TAURI_INTERNALS__,
  );
}

export function getBackendUrl(): string {
  if (getRunMode() === 'local') {
    return LOCAL_BACKEND_URL;
  }

  return readConfiguredRemoteUrl() ?? LOCAL_BACKEND_URL;
}

export function getRunMode(): RunMode {
  const storedMode = readStoredValue(RUN_MODE_KEY);
  if (storedMode === 'local' || storedMode === 'remote') {
    return storedMode;
  }

  return isTauriMode() ? 'local' : 'remote';
}

export function setRunMode(mode: RunMode): void {
  if (typeof localStorage === 'undefined') {
    return;
  }

  try {
    localStorage.setItem(RUN_MODE_KEY, mode);
  } catch {
    // Storage can be unavailable in hardened webviews.
  }
}

export function getRemoteBackendUrl(): string {
  return readConfiguredRemoteUrl() ?? '';
}

export function setRemoteBackendUrl(url: string): void {
  if (typeof localStorage === 'undefined') {
    return;
  }

  try {
    const normalized = trimTrailingSlash(url);
    if (normalized) {
      localStorage.setItem(REMOTE_BACKEND_URL_KEY, normalized);
    } else {
      localStorage.removeItem(REMOTE_BACKEND_URL_KEY);
    }
  } catch {
    // Storage can be unavailable in hardened webviews.
  }
}

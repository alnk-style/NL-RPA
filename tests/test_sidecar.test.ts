import { afterEach, describe, expect, it, vi } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';

import {
  getBackendUrl,
  isTauriMode,
  LOCAL_BACKEND_URL,
  REMOTE_BACKEND_URL_KEY,
  RUN_MODE_KEY,
} from '../desktop/src/lib/backend';

describe('desktop backend selection', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  function createLocalStorage(initial: Record<string, string> = {}) {
    const values = new Map(Object.entries(initial));

    return {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        values.set(key, value);
      },
      removeItem: (key: string) => {
        values.delete(key);
      },
    };
  }

  it('test_getBackendUrl_tauri_mode', () => {
    vi.stubGlobal('window', { __TAURI__: {} });
    vi.stubGlobal(
      'localStorage',
      createLocalStorage({
        [REMOTE_BACKEND_URL_KEY]: 'https://remote.example.com',
      }),
    );

    expect(getBackendUrl()).toBe(LOCAL_BACKEND_URL);
  });

  it('test_getBackendUrl_remote_mode', () => {
    vi.stubGlobal('window', {});
    vi.stubGlobal(
      'localStorage',
      createLocalStorage({
        [REMOTE_BACKEND_URL_KEY]: 'https://remote.example.com/',
      }),
    );

    expect(getBackendUrl()).toBe('https://remote.example.com');
  });

  it('uses the configured remote backend in Tauri remote mode', () => {
    vi.stubGlobal('window', { __TAURI__: {} });
    vi.stubGlobal(
      'localStorage',
      createLocalStorage({
        [RUN_MODE_KEY]: 'remote',
        [REMOTE_BACKEND_URL_KEY]: 'https://remote.example.com',
      }),
    );

    expect(getBackendUrl()).toBe('https://remote.example.com');
  });

  it('test_isTauriMode', () => {
    vi.stubGlobal('window', {});
    expect(isTauriMode()).toBe(false);

    vi.stubGlobal('window', { __TAURI__: {} });
    expect(isTauriMode()).toBe(true);
  });

  it('uses relative asset URLs in the packaged desktop build', () => {
    const html = readFileSync(
      new URL('../desktop/dist/index.html', import.meta.url),
      'utf8',
    );

    expect(html).not.toMatch(/(?:src|href)="\/(?!\/)/);
  });

  it('bundles only one React Router implementation', () => {
    const assetsUrl = new URL('../desktop/dist/assets/', import.meta.url);
    const routerBundle = readdirSync(assetsUrl).find(
      (name) =>
        name.startsWith('react-vendor-') && name.endsWith('.js'),
    );

    expect(routerBundle).toBeDefined();
    const source = readFileSync(new URL(routerBundle!, assetsUrl), 'utf8');
    const routerImplementationCount =
      source.match(/__reactRouterVersion/g)?.length ?? 0;

    expect(routerImplementationCount).toBe(1);
  });
});

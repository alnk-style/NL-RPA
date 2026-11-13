import { describe, expect, it } from 'vitest';

import {
  SCOPED_DESKTOP_TOOLS,
  filterDiscoverableApps,
  installedAppIconUrl,
} from '../frontend/src/lib/installed-app-mcp';

const apps = [
  {
    key: 'wps',
    name: 'WPS Office',
    executable_path: 'D:\\WPS\\WPS Office\\ksolaunch.exe',
    source: 'start_menu',
    icon_url: '/api/local-apps/discoverable-apps/wps/icon',
    generated_app_id: null,
  },
  {
    key: 'kugou',
    name: '酷狗音乐',
    executable_path: 'D:\\KGMusic\\KuGou.exe',
    source: 'start_menu',
    icon_url: '/api/local-apps/discoverable-apps/kugou/icon',
    generated_app_id: 'kugou_12345678',
  },
];

describe('installed application MCP helpers', () => {
  it('filters discovered applications by name and path', () => {
    expect(filterDiscoverableApps(apps, 'wps')).toEqual([apps[0]]);
    expect(filterDiscoverableApps(apps, 'kugou.exe')).toEqual([apps[1]]);
    expect(filterDiscoverableApps(apps, '  ')).toEqual(apps);
  });

  it('resolves authenticated icon URLs', () => {
    expect(
      installedAppIconUrl(apps[0].icon_url, 'http://127.0.0.1:8000'),
    ).toBe(
      'http://127.0.0.1:8000/api/local-apps/discoverable-apps/wps/icon',
    );
  });

  it('exposes the generated scoped tool catalog', () => {
    expect(SCOPED_DESKTOP_TOOLS.map((tool) => tool.name)).toEqual([
      'app_launch',
      'app_observe',
      'app_activate',
      'app_read_text',
    ]);
  });
});

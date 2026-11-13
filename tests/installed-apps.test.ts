import { describe, expect, it } from 'vitest';

import {
  filterInstalledApps,
  installedAppIconUrl,
} from '../desktop/src/lib/installed-apps';

const apps = [
  {
    key: 'wps',
    name: 'WPS Office',
    executable_path: 'D:\\WPS\\WPS Office\\ksolaunch.exe',
    source: 'start_menu',
    icon_url: '/api/agents/1/config/installed-apps/wps/icon',
  },
  {
    key: 'kugou',
    name: '酷狗音乐',
    executable_path: 'D:\\KGMusic\\KuGou.exe',
    source: 'start_menu',
    icon_url: '/api/agents/1/config/installed-apps/kugou/icon',
  },
];

describe('installed application picker helpers', () => {
  it('matches application names and executable paths case-insensitively', () => {
    expect(filterInstalledApps(apps, 'wps')).toEqual([apps[0]]);
    expect(filterInstalledApps(apps, 'kugou.exe')).toEqual([apps[1]]);
    expect(filterInstalledApps(apps, '  ')).toEqual(apps);
  });

  it('resolves icon URLs against the backend origin', () => {
    expect(
      installedAppIconUrl(
        '/api/agents/1/config/installed-apps/wps/icon',
        'http://127.0.0.1:8000',
      ),
    ).toBe(
      'http://127.0.0.1:8000/api/agents/1/config/installed-apps/wps/icon',
    );
  });
});

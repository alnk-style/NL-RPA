import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(testDirectory, '..');

function read(relativePath: string): string {
  return fs.readFileSync(
    path.join(projectRoot, relativePath),
    'utf8',
  );
}

function expectRouteOrder(source: string, route: string): void {
  const routeIndex = source.indexOf(`path="${route}"`);
  const wildcardIndex = source.indexOf('path="*"');
  expect(routeIndex).toBeGreaterThan(-1);
  expect(wildcardIndex).toBeGreaterThan(-1);
  expect(routeIndex).toBeLessThan(wildcardIndex);
}

describe('desktop and web route parity', () => {
  it('desktop uses its App entry and registers shared application routes', () => {
    const main = read('desktop/src/main.tsx');
    const desktopApp = read('desktop/src/App.tsx');

    expect(main).toContain("from './App'");
    expect(desktopApp).toContain(
      "import { AppsPage } from '../../frontend/src/pages/AppsPage'",
    );

    for (const route of [
      '/dashboard',
      '/chat',
      '/apps',
      '/workflows',
      '/workflows/:id',
      '/executions',
      '/executions/:id',
      '/pending',
      '/connections',
      '/advanced/tools',
      '/settings',
      '/desktop-settings',
    ]) {
      expect(desktopApp).toContain(`path="${route}"`);
    }
    expectRouteOrder(desktopApp, '/apps');
  });

  it('desktop entry provides React Query to shared pages', () => {
    const main = read('desktop/src/main.tsx');

    expect(main).toContain(
      "import { QueryProvider } from '../../frontend/src/app/queryClient'",
    );
    expect(main).toMatch(
      /<QueryProvider>\s*<App \/>\s*<\/QueryProvider>/,
    );
  });

  it('root web app and shared layout retain the applications route', () => {
    const webApp = read('frontend/src/App.tsx');
    const layout = read('frontend/src/components/Layout.tsx');

    expectRouteOrder(webApp, '/apps');
    expect(layout).toContain("to: '/apps'");
    expect(layout).toContain("label: '应用'");
  });
});

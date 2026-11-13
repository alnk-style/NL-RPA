export interface AppRouteDefinition {
  path: string;
  key:
    | 'dashboard'
    | 'workflows'
    | 'studio'
    | 'executions'
    | 'execution'
    | 'pending'
    | 'connections'
    | 'settings'
    | 'advanced-tools'
    | 'advanced-chat'
    | 'apps'
    | 'login'
    | 'fallback';
  lazy: boolean;
  nav: boolean;
}

export const APP_ROUTES: AppRouteDefinition[] = [
  { path: '/login', key: 'login', lazy: false, nav: false },
  { path: '/', key: 'dashboard', lazy: true, nav: true },
  { path: '/workflows', key: 'workflows', lazy: true, nav: true },
  {
    path: '/workflows/:id/studio',
    key: 'studio',
    lazy: true,
    nav: false,
  },
  { path: '/executions', key: 'executions', lazy: true, nav: true },
  {
    path: '/executions/:id',
    key: 'execution',
    lazy: true,
    nav: false,
  },
  { path: '/pending', key: 'pending', lazy: true, nav: true },
  {
    path: '/connections',
    key: 'connections',
    lazy: true,
    nav: true,
  },
  { path: '/settings', key: 'settings', lazy: true, nav: true },
  {
    path: '/advanced/tools',
    key: 'advanced-tools',
    lazy: true,
    nav: true,
  },
  {
    path: '/advanced/chat',
    key: 'advanced-chat',
    lazy: true,
    nav: false,
  },
  { path: '/apps', key: 'apps', lazy: true, nav: true },
  { path: '*', key: 'fallback', lazy: false, nav: false },
];

export function isNavigationItemActive(
  pathname: string,
  target: string,
): boolean {
  const current = pathname.replace(/\/+$/, '') || '/';
  const normalizedTarget = target.replace(/\/+$/, '') || '/';
  if (normalizedTarget === '/') {
    return current === '/';
  }
  return current === normalizedTarget
    || current.startsWith(`${normalizedTarget}/`);
}

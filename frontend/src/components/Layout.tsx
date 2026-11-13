import {
  Activity,
  Bot,
  Boxes,
  Hand,
  LayoutDashboard,
  ListChecks,
  PlugZap,
  Settings,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { getCurrentUser } from '../api/auth';
import { isNavigationItemActive } from '../app/router';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

export interface NavigationItem {
  label: string;
  to: string;
  icon: LucideIcon;
}

const navigationItems: NavigationItem[] = [
  {
    label: '工作台',
    to: '/',
    icon: LayoutDashboard,
  },
  {
    label: '工作流',
    to: '/workflows',
    icon: ListChecks,
  },
  {
    label: '执行记录',
    to: '/executions',
    icon: Activity,
  },
  {
    label: '待处理',
    to: '/pending',
    icon: Hand,
  },
  {
    label: '连接与凭据',
    to: '/connections',
    icon: PlugZap,
  },
  {
    label: '设置',
    to: '/settings',
    icon: Settings,
  },
  {
    label: '高级工具',
    to: '/advanced/tools',
    icon: Wrench,
  },
  {
    label: '应用',
    to: '/apps',
    icon: Boxes,
  },
];

function NavigationLink({
  item,
  compact = false,
}: {
  item: NavigationItem;
  compact?: boolean;
}) {
  const Icon = item.icon;
  const { pathname } = useLocation();
  const active = isNavigationItemActive(pathname, item.to);

  return (
    <NavLink
      to={item.to}
      className={() =>
        cn(
          'flex items-center rounded-md font-medium transition-colors',
          compact
            ? 'flex-1 flex-col gap-1 px-2 py-2 text-[11px]'
            : 'gap-3 px-3 py-2.5 text-sm',
          active
            ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-100'
            : 'text-slate-500 hover:bg-slate-100 hover:text-slate-950 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white',
        )
      }
    >
      <Icon className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
      <span>{item.label}</span>
    </NavLink>
  );
}

export function Layout({
  additionalNavigationItems = [],
}: {
  additionalNavigationItems?: NavigationItem[];
}) {
  const isDarkMode = useStore((state) => state.isDarkMode);
  const user = useStore((state) => state.user);
  const setUser = useStore((state) => state.setUser);
  const items = [...navigationItems, ...additionalNavigationItems];

  useEffect(() => {
    if (user) {
      return;
    }

    let cancelled = false;

    void getCurrentUser()
      .then((currentUser) => {
        if (!cancelled) {
          setUser(currentUser);
        }
      })
      .catch(() => {
        // The response interceptor handles unauthenticated requests.
      });

    return () => {
      cancelled = true;
    };
  }, [setUser, user]);

  return (
    <div
      className={cn(
        'min-h-screen bg-slate-50 text-slate-950 dark:bg-slate-950 dark:text-white',
        isDarkMode && 'dark',
      )}
    >
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col dark:border-slate-800 dark:bg-slate-900">
        <div className="flex h-16 items-center gap-3 border-b border-slate-200 px-5 dark:border-slate-800">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-600 text-white">
            <Bot className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold leading-5">个人自动化</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              RPA 工作台
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 p-3" aria-label="主导航">
          {items.map((item) => (
            <NavigationLink key={item.to} item={item} />
          ))}
        </nav>

        <div className="border-t border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-center gap-3 rounded-md px-3 py-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-200">
              {user?.username?.charAt(0).toUpperCase() ?? 'U'}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {user?.username ?? '未登录'}
              </p>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                {user?.email ?? '等待身份验证'}
              </p>
            </div>
          </div>
        </div>
      </aside>

      <div className="min-h-screen lg:pl-64">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-3 py-2 backdrop-blur lg:hidden dark:border-slate-800 dark:bg-slate-900/95">
          <nav
            className="flex items-center gap-1 overflow-x-auto"
            aria-label="移动端主导航"
          >
            {items.map((item) => (
              <NavigationLink key={item.to} item={item} compact />
            ))}
          </nav>
        </header>

        <main className="mx-auto min-h-screen w-full max-w-[1600px] p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default Layout;

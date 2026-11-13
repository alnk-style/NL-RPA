import type { ReactNode } from 'react';

export function StudioLayout({
  topBar,
  outline,
  canvas,
  inspector,
  drawer,
}: {
  topBar: ReactNode;
  outline: ReactNode;
  canvas: ReactNode;
  inspector: ReactNode;
  drawer: ReactNode;
}) {
  return (
    <div className="-m-4 flex h-[calc(100vh-4rem)] min-h-[680px] flex-col overflow-hidden border-y border-slate-200 bg-slate-100 sm:-m-6 lg:-m-8 dark:border-slate-800 dark:bg-slate-950">
      {topBar}
      <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)_320px] overflow-hidden">
        <div className="min-h-0 border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          {outline}
        </div>
        <main className="min-h-0 overflow-auto bg-slate-50 dark:bg-slate-950">
          {canvas}
        </main>
        <div className="min-h-0 border-l border-slate-200 dark:border-slate-800">
          {inspector}
        </div>
      </div>
      <div className="max-h-[34vh] min-h-[170px] overflow-auto border-t border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        {drawer}
      </div>
    </div>
  );
}

export default StudioLayout;

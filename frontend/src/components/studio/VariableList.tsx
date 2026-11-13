import { Braces } from 'lucide-react';

import type { VariableChoice } from './VariablePicker';

export function VariableList({
  variables,
}: {
  variables: VariableChoice[];
}) {
  return (
    <div className="border-t border-slate-200 p-3 dark:border-slate-800">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <Braces className="h-3.5 w-3.5" />
        变量
      </h2>
      {variables.length ? (
        <div className="space-y-1">
          {variables.map((variable) => (
            <p
              key={variable.name}
              className="truncate rounded bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              title={variable.expression}
            >
              {variable.expression}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-xs text-slate-500">暂无变量</p>
      )}
    </div>
  );
}

export default VariableList;

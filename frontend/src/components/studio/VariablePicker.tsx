export interface VariableChoice {
  name: string;
  expression: string;
}

export function VariablePicker({
  variables,
  onPick,
}: {
  variables: VariableChoice[];
  onPick: (expression: string) => void;
}) {
  if (!variables.length) {
    return <p className="text-xs text-slate-500">暂无可用变量</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {variables.map((variable) => (
        <button
          key={variable.name}
          type="button"
          className="rounded border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          onClick={() => onPick(variable.expression)}
        >
          {variable.expression}
        </button>
      ))}
    </div>
  );
}

export default VariablePicker;

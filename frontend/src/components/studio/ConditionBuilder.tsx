import { useState } from 'react';
import { buildCondition } from '../../lib/action-schema';

const inputClass =
  'h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900';

export function ConditionBuilder({
  onChange,
}: {
  onChange: (condition: Record<string, unknown>) => void;
}) {
  const [variable, setVariable] = useState('');
  const [operator, setOperator] = useState('eq');
  const [value, setValue] = useState('');
  const [cast, setCast] = useState('');
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <input
        className={inputClass}
        placeholder="变量路径"
        value={variable}
        onChange={(event) => setVariable(event.target.value)}
      />
      <select
        className={inputClass}
        value={operator}
        onChange={(event) => setOperator(event.target.value)}
      >
        {['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'in'].map(
          (item) => <option key={item}>{item}</option>,
        )}
      </select>
      <input
        className={inputClass}
        placeholder="比较值"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <select
        className={inputClass}
        value={cast}
        onChange={(event) => setCast(event.target.value)}
      >
        <option value="">不转换</option>
        {['string', 'number', 'integer', 'boolean', 'date'].map(
          (item) => <option key={item}>{item}</option>,
        )}
      </select>
      <button
        type="button"
        className="h-8 rounded-md bg-slate-900 px-3 text-xs font-medium text-white sm:col-span-2"
        disabled={!variable.trim()}
        onClick={() => onChange(buildCondition({
          variable,
          operator,
          value,
          cast: cast || undefined,
        }))}
      >
        应用条件
      </button>
    </div>
  );
}

export default ConditionBuilder;

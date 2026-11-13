import type { ActionFormField } from '../../lib/action-catalog';

const inputClass =
  'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

export interface ActionCatalogFormProps {
  fields: ActionFormField[];
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  onVariableRequest?: () => void;
}

export function ActionCatalogForm({
  fields,
  values,
  onChange,
  onVariableRequest,
}: ActionCatalogFormProps) {
  function update(key: string, value: unknown) {
    const next = { ...values };
    if (value === undefined || value === '') {
      delete next[key];
    } else {
      next[key] = value;
    }
    onChange(next);
  }

  if (fields.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-slate-200 px-3 py-3 text-xs text-slate-500 dark:border-slate-700">
        此动作没有可自动生成的字段，请使用下方参数 JSON。
      </p>
    );
  }

  return (
    <div className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
      {fields.map((field) => {
        const value = values[field.key];
        return (
          <div key={field.key} className="space-y-1.5 px-3 py-3">
            <div className="flex items-center justify-between gap-2">
              <label
                htmlFor={`action-${field.key}`}
                className="break-all text-xs font-medium text-slate-700 dark:text-slate-200"
              >
                {field.label}
                {field.required ? <span className="ml-1 text-red-500">*</span> : null}
              </label>
              <span className="text-[10px] uppercase text-slate-400">
                {field.control}
              </span>
            </div>
            {field.control === 'boolean' ? (
              <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                <input
                  id={`action-${field.key}`}
                  type="checkbox"
                  checked={value === true}
                  onChange={(event) => update(field.key, event.target.checked)}
                />
                {value === true ? '是' : '否'}
              </label>
            ) : field.control === 'enum' ? (
              <select
                id={`action-${field.key}`}
                className={inputClass}
                value={typeof value === 'string' ? value : ''}
                onChange={(event) => update(field.key, event.target.value)}
              >
                <option value="">请选择</option>
                {field.options.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            ) : field.control === 'number' ? (
              <input
                id={`action-${field.key}`}
                type="number"
                className={inputClass}
                value={typeof value === 'number' || typeof value === 'string' ? String(value) : ''}
                onChange={(event) => {
                  const raw = event.target.value;
                  update(field.key, raw === '' ? undefined : Number(raw));
                }}
              />
            ) : field.control === 'json' ? (
              <textarea
                id={`action-${field.key}`}
                className="min-h-20 w-full rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100 outline-none focus:border-brand-500 dark:border-slate-700"
                value={value === undefined ? '' : JSON.stringify(value, null, 2)}
                placeholder="${variable} 或 JSON"
                onChange={(event) => {
                  const raw = event.target.value.trim();
                  if (!raw) {
                    update(field.key, undefined);
                    return;
                  }
                  try {
                    update(field.key, JSON.parse(raw) as unknown);
                  } catch {
                    update(field.key, event.target.value);
                  }
                }}
              />
            ) : (
              <div className="flex gap-2">
                <input
                  id={`action-${field.key}`}
                  className={inputClass}
                  value={typeof value === 'string' ? value : value === undefined ? '' : String(value)}
                  placeholder={field.description || '${variable}'}
                  onChange={(event) => update(field.key, event.target.value)}
                />
                {onVariableRequest ? (
                  <button
                    type="button"
                    className="h-9 shrink-0 rounded-md border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                    onClick={onVariableRequest}
                  >
                    {'${}'}
                  </button>
                ) : null}
              </div>
            )}
            {field.description ? (
              <p className="break-words text-[11px] leading-4 text-slate-400">
                {field.description}
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export default ActionCatalogForm;

import type {
  StudioSchemaField,
} from '../../lib/action-schema';
import { VariablePicker, type VariableChoice } from './VariablePicker';

const inputClass =
  'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900';

export function ActionSchemaForm({
  fields,
  values,
  variables,
  credentials,
  onChange,
}: {
  fields: StudioSchemaField[];
  values: Record<string, unknown>;
  variables: VariableChoice[];
  credentials: string[];
  onChange: (next: Record<string, unknown>) => void;
}) {
  function update(key: string, value: unknown) {
    const next = { ...values };
    if (value === undefined || value === '') {
      delete next[key];
    } else {
      next[key] = value;
    }
    onChange(next);
  }

  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const value = values[field.key];
        return (
          <label key={field.key} className="block space-y-1.5">
            <span className="flex items-center justify-between gap-2 text-xs font-medium text-slate-600 dark:text-slate-300">
              <span>
                {field.label}
                {field.required ? <span className="ml-1 text-red-500">*</span> : null}
              </span>
              {field.fallback ? (
                <span className="text-[10px] font-normal text-amber-600">JSON</span>
              ) : null}
            </span>
            {field.control === 'switch' ? (
              <input
                type="checkbox"
                checked={value === true}
                onChange={(event) => update(field.key, event.target.checked)}
              />
            ) : field.control === 'select' ? (
              <select
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
                type="number"
                className={inputClass}
                value={typeof value === 'number' || typeof value === 'string' ? String(value) : ''}
                onChange={(event) => update(field.key, Number(event.target.value))}
              />
            ) : field.control === 'variable' ? (
              <div className="space-y-2">
                <input
                  className={inputClass}
                  value={typeof value === 'string' ? value : ''}
                  onChange={(event) => update(field.key, event.target.value)}
                  placeholder="${variable}"
                />
                <VariablePicker
                  variables={variables}
                  onPick={(expression) => update(field.key, expression)}
                />
              </div>
            ) : field.control === 'credential' ? (
              <select
                className={inputClass}
                value={typeof value === 'string' ? value : ''}
                onChange={(event) => update(field.key, event.target.value)}
              >
                <option value="">选择凭据</option>
                {credentials.map((credential) => (
                  <option key={credential} value={credential}>{credential}</option>
                ))}
              </select>
            ) : field.control === 'condition' ? (
              <textarea
                className="min-h-20 w-full rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100"
                value={value ? JSON.stringify(value, null, 2) : ''}
                onChange={(event) => {
                  try {
                    update(field.key, JSON.parse(event.target.value));
                  } catch {
                    // Keep the last valid declarative condition.
                  }
                }}
              />
            ) : ['textarea', 'array', 'object', 'json'].includes(field.control) ? (
              <textarea
                className="min-h-20 w-full rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100"
                value={
                  typeof value === 'string'
                    ? value
                    : value === undefined
                      ? ''
                      : JSON.stringify(value, null, 2)
                }
                onChange={(event) => {
                  if (field.control === 'textarea') {
                    update(field.key, event.target.value);
                    return;
                  }
                  try {
                    update(field.key, JSON.parse(event.target.value));
                  } catch {
                    update(field.key, event.target.value);
                  }
                }}
              />
            ) : (
              <input
                className={inputClass}
                value={value === undefined ? '' : String(value)}
                onChange={(event) => update(field.key, event.target.value)}
              />
            )}
            {field.description ? (
              <span className="block text-[11px] text-slate-400">
                {field.description}
              </span>
            ) : null}
          </label>
        );
      })}
    </div>
  );
}

export default ActionSchemaForm;

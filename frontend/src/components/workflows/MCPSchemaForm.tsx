import { Plus, Trash2 } from 'lucide-react';

import type { MCPFormField } from '../../lib/mcp-workflow';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';

const inputClass =
  'h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

export interface MCPSchemaFormProps {
  fields: MCPFormField[];
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}

export function MCPSchemaForm({
  fields,
  values,
  onChange,
}: MCPSchemaFormProps) {
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
        该工具未声明输入参数，可直接使用空参数对象。
      </p>
    );
  }

  return (
    <div className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
      {fields.map((field) => {
        const value = values[field.key];
        return (
          <div
            key={field.key}
            className="min-w-0 space-y-1.5 px-3 py-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-1">
              <label
                htmlFor={`mcp-field-${field.key}`}
                className="break-all text-xs font-medium text-slate-700 dark:text-slate-200"
              >
                {field.label}
                {field.required ? (
                  <span className="ml-1 text-red-500">*</span>
                ) : null}
              </label>
              <span className="text-[10px] uppercase text-slate-400">
                {field.control}
              </span>
            </div>

            {field.control === 'boolean' ? (
              <div className="flex items-center gap-2">
                <Switch
                  checked={value === true}
                  onCheckedChange={(checked) => update(field.key, checked)}
                  label={field.label}
                />
                <span className="text-xs text-slate-500">
                  {value === true ? '是' : '否'}
                </span>
              </div>
            ) : field.control === 'enum' ? (
              <select
                id={`mcp-field-${field.key}`}
                className={inputClass}
                value={typeof value === 'string' ? value : ''}
                onChange={(event) =>
                  update(field.key, event.target.value)
                }
              >
                <option value="">请选择</option>
                {field.options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : field.control === 'number' ? (
              <input
                id={`mcp-field-${field.key}`}
                type="number"
                className={inputClass}
                value={
                  typeof value === 'number' || typeof value === 'string'
                    ? String(value)
                    : ''
                }
                onChange={(event) => {
                  const raw = event.target.value;
                  update(
                    field.key,
                    raw === '' ? undefined : Number(raw),
                  );
                }}
              />
            ) : field.control === 'string_list' ? (
              <StringListField
                id={`mcp-field-${field.key}`}
                value={
                  Array.isArray(value)
                    ? value.map(String)
                    : []
                }
                label={field.label}
                onChange={(items) => update(field.key, items)}
              />
            ) : field.control === 'json' ? (
              <p className="rounded bg-slate-50 px-2.5 py-2 text-xs text-slate-500 dark:bg-slate-950/40">
                复杂参数，请使用下方“参数 JSON”编辑。
              </p>
            ) : (
              <input
                id={`mcp-field-${field.key}`}
                className={inputClass}
                value={typeof value === 'string' ? value : ''}
                placeholder={field.description}
                onChange={(event) =>
                  update(field.key, event.target.value)
                }
              />
            )}

            {field.description && field.description !== field.label ? (
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

function StringListField({
  id,
  value,
  label,
  onChange,
}: {
  id: string;
  value: string[];
  label: string;
  onChange: (value: string[]) => void;
}) {
  const items = value.length > 0 ? value : [''];
  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <div key={`${id}-${index}`} className="flex items-center gap-2">
          <input
            id={index === 0 ? id : undefined}
            className={inputClass}
            value={item}
            aria-label={`${label} ${index + 1}`}
            onChange={(event) => {
              const next = [...items];
              next[index] = event.target.value;
              onChange(next.filter((entry, entryIndex) => (
                entry.trim() !== '' || entryIndex === 0
              )));
            }}
          />
          <button
            type="button"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600"
            onClick={() => onChange(items.filter((_, itemIndex) => (
              itemIndex !== index
            )))}
            aria-label={`删除 ${label} 第 ${index + 1} 项`}
            title="删除此项"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      ))}
      <Button
        variant="ghost"
        size="sm"
        leftIcon={<Plus className="h-3.5 w-3.5" />}
        onClick={() => onChange([...items, ''])}
      >
        添加一项
      </Button>
    </div>
  );
}

export default MCPSchemaForm;


import { useState } from 'react';

export function JsonAdvancedEditor({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      <textarea
        className="min-h-48 w-full rounded-md border border-slate-200 bg-slate-950 p-3 font-mono text-xs text-slate-100"
        value={text}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          try {
            const parsed = JSON.parse(next) as unknown;
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
              setError('参数必须是 JSON 对象');
              return;
            }
            setError(null);
            onChange(parsed as Record<string, unknown>);
          } catch {
            setError('JSON 格式不完整');
          }
        }}
      />
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
    </div>
  );
}

export default JsonAdvancedEditor;

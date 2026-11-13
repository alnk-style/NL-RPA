import {
  dataPreviewCounts,
  dataPreviewModel,
} from '../../lib/data-preview';

export function DataPreview({
  value,
  stepLogs,
}: {
  value: unknown;
  stepLogs?: unknown;
}) {
  const model = dataPreviewModel(value);
  const counts = dataPreviewCounts(stepLogs);
  if (!model.rowCount) {
    return (
      <div className="flex min-h-28 items-center justify-center text-sm text-slate-500">
        没有可预览的数据
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        {counts ? (
          <span>过滤 {counts.inputCount} → {counts.outputCount}</span>
        ) : null}
        <span>行数 {model.rowCount}</span>
        <span>空值 {model.emptyCount}</span>
        <span>重复 {model.duplicateCount}</span>
      </div>
      <div className="max-h-64 overflow-auto rounded border border-slate-200 dark:border-slate-800">
        <table className="min-w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-slate-100 dark:bg-slate-800">
            <tr>
              {model.columns.map((column) => (
                <th key={column.key} className="whitespace-nowrap px-2 py-1.5">
                  {column.label}
                  <span className="ml-1 font-normal text-slate-400">
                    {column.type}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {model.rows.map((row, index) => (
              <tr key={index} className="border-t border-slate-100 dark:border-slate-800">
                {model.columns.map((column) => (
                  <td key={column.key} className="max-w-[260px] truncate px-2 py-1.5">
                    {String(row[column.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataPreview;

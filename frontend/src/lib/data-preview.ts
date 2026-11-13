export interface DataPreviewColumn {
  key: string;
  label: string;
  type: string;
}

export interface DataPreviewModel {
  rows: Record<string, unknown>[];
  columns: DataPreviewColumn[];
  rowCount: number;
  previewRowCount: number;
  truncated: boolean;
  emptyCount: number;
  duplicateCount: number;
}

export interface DataPreviewCounts {
  inputCount: number;
  outputCount: number;
}

export function dataPreviewModel(value: unknown): DataPreviewModel {
  const rows = Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item)
          && typeof item === 'object'
          && !Array.isArray(item),
      )
    : [];
  const keys = Array.from(
    new Set(rows.flatMap((row) => Object.keys(row))),
  );
  const serialized = rows.map((row) => JSON.stringify(row));
  const previewRows = rows.slice(0, 50);
  return {
    rows: previewRows,
    columns: keys.map((key) => ({
      key,
      label: key,
      type: Array.from(
        new Set(
          rows
            .map((row) => typeof row[key])
            .filter((type) => type !== 'undefined'),
        ),
      ).join(' | ') || 'unknown',
    })),
    rowCount: rows.length,
    previewRowCount: previewRows.length,
    truncated: rows.length > previewRows.length,
    emptyCount: rows.reduce(
      (count, row) => count + keys.filter(
        (key) => row[key] === null
          || row[key] === undefined
          || row[key] === '',
      ).length,
      0,
    ),
    duplicateCount: serialized.length - new Set(serialized).size,
  };
}

function asCount(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }
  return null;
}

export function dataPreviewCounts(
  stepLogs: unknown,
): DataPreviewCounts | null {
  if (!Array.isArray(stepLogs)) {
    return null;
  }
  for (const entry of [...stepLogs].reverse()) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      continue;
    }
    const log = entry as Record<string, unknown>;
    const verification = (
      log.verification
      && typeof log.verification === 'object'
      && !Array.isArray(log.verification)
    )
      ? log.verification as Record<string, unknown>
      : {};
    const inputCount = asCount(
      verification.input_count ?? log.input_count,
    );
    const outputCount = asCount(
      verification.output_count ?? log.output_count,
    );
    if (inputCount !== null && outputCount !== null) {
      return { inputCount, outputCount };
    }
  }
  return null;
}

export default dataPreviewModel;

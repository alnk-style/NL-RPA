import type { WorkflowVersionChange } from '../types/workflow';

export type VersionChangeKind = 'add' | 'remove' | 'change' | 'unknown';

export interface VersionChangeGroup {
  kind: VersionChangeKind;
  changes: WorkflowVersionChange[];
}

export interface GroupedVersionChanges {
  groups: VersionChangeGroup[];
  counts: Record<VersionChangeKind | 'total', number>;
}

const ORDER: VersionChangeKind[] = [
  'change',
  'add',
  'remove',
  'unknown',
];

function normalizeKind(kind: unknown): VersionChangeKind {
  return kind === 'add' || kind === 'remove' || kind === 'change'
    ? kind
    : 'unknown';
}

export function groupVersionChanges(
  changes: WorkflowVersionChange[],
): GroupedVersionChanges {
  const buckets: Record<VersionChangeKind, WorkflowVersionChange[]> = {
    add: [],
    remove: [],
    change: [],
    unknown: [],
  };
  for (const change of changes) {
    buckets[normalizeKind(change.kind)].push(change);
  }
  const groups = ORDER
    .filter((kind) => buckets[kind].length > 0)
    .map((kind) => ({ kind, changes: buckets[kind] }));
  return {
    groups,
    counts: {
      add: buckets.add.length,
      remove: buckets.remove.length,
      change: buckets.change.length,
      unknown: buckets.unknown.length,
      total: changes.length,
    },
  };
}

export function formatVersionChangeValue(value: unknown): string {
  if (value === undefined) {
    return '未设置';
  }
  if (typeof value === 'string') {
    return JSON.stringify(value);
  }
  return JSON.stringify(value);
}

export default groupVersionChanges;

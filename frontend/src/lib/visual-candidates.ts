export interface VisualCandidateView {
  candidate_id: string;
  target: string;
  bbox: [number, number, number, number];
  confidence: number;
  evidence: string;
  expected_after: string;
}

export function visualCandidatesFromTask(task: {
  kind?: string;
  payload?: Record<string, unknown> | null;
}): VisualCandidateView[] {
  if (task.kind !== 'visual_ambiguous' || !task.payload) {
    return [];
  }
  const raw = task.payload.candidates;
  if (!Array.isArray(raw)) {
    return [];
  }
  const candidates: VisualCandidateView[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const candidateId = String(record.candidate_id ?? '').trim();
    const bbox = Array.isArray(record.bbox)
      ? record.bbox.map(Number)
      : [];
    const confidence = Number(record.confidence);
    if (
      !candidateId
      || bbox.length !== 4
      || bbox.some((value) => !Number.isFinite(value))
      || !Number.isFinite(confidence)
    ) {
      continue;
    }
    candidates.push({
      candidate_id: candidateId,
      target: String(record.target ?? '').trim() || '未命名目标',
      bbox: bbox as [number, number, number, number],
      confidence,
      evidence: String(record.evidence ?? ''),
      expected_after: String(record.expected_after ?? ''),
    });
  }
  return candidates;
}

export function buildVisualCandidateVerification(
  candidateId: string,
): Record<string, unknown> {
  const value = String(candidateId ?? '').trim();
  return value ? { candidate_id: value } : {};
}

export function candidateOverlayStyle(
  bbox: [number, number, number, number],
  width: number,
  height: number,
): Record<string, string> {
  if (!width || !height) {
    return {};
  }
  const [x1, y1, x2, y2] = bbox;
  return {
    left: `${(x1 / width) * 100}%`,
    top: `${(y1 / height) * 100}%`,
    width: `${((x2 - x1) / width) * 100}%`,
    height: `${((y2 - y1) / height) * 100}%`,
  };
}

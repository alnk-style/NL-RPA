import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildVisualCandidateVerification,
  candidateOverlayStyle,
  visualCandidatesFromTask,
} from '../src/lib/visual-candidates.ts';

const task = {
  kind: 'visual_ambiguous',
  payload: {
    image_width: 1000,
    image_height: 500,
    candidates: [
      {
        candidate_id: 'cand_1',
        target: '导出',
        bbox: [100, 50, 300, 150],
        confidence: 0.94,
        evidence: '按钮文字',
      },
      {
        candidate_id: 'cand_2',
        target: '导出全部',
        bbox: [100, 200, 300, 300],
        confidence: 0.92,
        evidence: '按钮文字',
      },
    ],
  },
};

test('visual candidates are parsed without exposing editable coordinates', () => {
  const candidates = visualCandidatesFromTask(task);
  assert.equal(candidates.length, 2);
  assert.equal(candidates[0].candidate_id, 'cand_1');
  assert.deepEqual(
    buildVisualCandidateVerification('cand_1'),
    { candidate_id: 'cand_1' },
  );
});

test('candidate overlay uses read-only percentage positioning', () => {
  assert.deepEqual(
    candidateOverlayStyle([100, 50, 300, 150], 1000, 500),
    {
      left: '10%',
      top: '10%',
      width: '20%',
      height: '20%',
    },
  );
});

test('legacy visual tasks without candidate payload stay displayable', () => {
  assert.deepEqual(
    visualCandidatesFromTask({
      kind: 'visual_ambiguous',
      payload: {},
    }),
    [],
  );
  assert.deepEqual(
    visualCandidatesFromTask({ kind: 'login_required', payload: {} }),
    [],
  );
});

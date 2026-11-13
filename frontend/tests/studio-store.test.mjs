import assert from 'node:assert/strict';
import test from 'node:test';

import { useWorkflowStudioStore } from '../src/store/useWorkflowStudioStore.ts';

function reset() {
  useWorkflowStudioStore.getState().reset();
}

test('patchStep marks dirty and undo redo restore exact draft', () => {
  reset();
  const plan = {
    execution_mode: 'structured',
    variables: [],
    steps: [
      {
        step_id: 1,
        executor: 'web',
        action: 'goto',
        params: { url: 'https://example.com' },
        depends_on: [],
      },
    ],
  };
  const store = useWorkflowStudioStore.getState();
  store.load(
    {
      id: 4,
      name: 'Workflow',
      status: 'draft',
      structured_plan: plan,
      draft_revision: 2,
    },
    null,
  );
  useWorkflowStudioStore.getState().patchStep(1, {
    params: { url: 'https://changed.example' },
  });
  assert.equal(useWorkflowStudioStore.getState().dirty, true);
  assert.equal(
    useWorkflowStudioStore.getState().draft.steps[0].params.url,
    'https://changed.example',
  );
  useWorkflowStudioStore.getState().undo();
  assert.deepEqual(useWorkflowStudioStore.getState().draft, plan);
  useWorkflowStudioStore.getState().redo();
  assert.equal(
    useWorkflowStudioStore.getState().draft.steps[0].params.url,
    'https://changed.example',
  );
});

test('loading another workflow resets history and selection', () => {
  reset();
  const store = useWorkflowStudioStore.getState();
  store.load(
    {
      id: 1,
      name: 'One',
      status: 'draft',
      structured_plan: { steps: [] },
      draft_revision: 0,
    },
    null,
  );
  store.addStep({
    step_id: 1,
    executor: 'web',
    action: 'goto',
    params: {},
  });
  useWorkflowStudioStore.getState().load(
    {
      id: 2,
      name: 'Two',
      status: 'draft',
      structured_plan: { steps: [] },
      draft_revision: 4,
    },
    null,
  );
  assert.equal(useWorkflowStudioStore.getState().workflowId, 2);
  assert.equal(useWorkflowStudioStore.getState().dirty, false);
  assert.equal(useWorkflowStudioStore.getState().canUndo, false);
});

test('save failure preserves the dirty draft and base revision', () => {
  reset();
  const store = useWorkflowStudioStore.getState();
  store.load(
    {
      id: 3,
      name: 'Conflict',
      status: 'draft',
      structured_plan: {
        execution_mode: 'structured',
        variables: [],
        steps: [
          {
            step_id: 1,
            executor: 'transform',
            action: 'filter',
            params: { value: 1000 },
          },
        ],
      },
      draft_revision: 7,
    },
    null,
  );
  useWorkflowStudioStore.getState().patchStep(1, {
    params: { value: 2000 },
  });
  useWorkflowStudioStore.getState().markSaveFailed();

  const state = useWorkflowStudioStore.getState();
  assert.equal(state.dirty, true);
  assert.equal(state.baseRevision, 7);
  assert.equal(state.draft.steps[0].params.value, 2000);
});

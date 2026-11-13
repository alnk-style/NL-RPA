import assert from 'node:assert/strict';
import test from 'node:test';

import { saveWorkflowDraft } from '../src/lib/workflow-draft.ts';

function createdWorkflow(id) {
  return {
    id,
    user_id: 1,
    name: 'Draft workflow',
    description: null,
    natural_language_input: 'Create a document',
    structured_plan: null,
    generated_code: null,
    dag_definition: null,
    trigger_type: 'manual',
    cron_expression: null,
    status: 'draft',
    notification_config: null,
    executor_type: 'windows',
    required_capabilities: [],
    created_at: '2026-09-24T00:00:00.000Z',
    updated_at: '2026-09-24T00:00:00.000Z',
  };
}

function notFoundError() {
  return {
    isAxiosError: true,
    response: { status: 404 },
  };
}

test('saveWorkflowDraft recreates a missing workflow', async () => {
  const calls = [];
  const api = {
    createWorkflow: async (payload) => {
      calls.push(['create', payload]);
      return createdWorkflow(9);
    },
    updateWorkflow: async (workflowId, payload) => {
      calls.push(['update', workflowId, payload]);
      if (workflowId === 1) {
        throw notFoundError();
      }
      return { ...createdWorkflow(workflowId), structured_plan: payload.structured_plan };
    },
  };

  const result = await saveWorkflowDraft(api, {
    workflowId: 1,
    createPayload: {
      name: 'Draft workflow',
      natural_language_input: 'Create a document',
    },
    updatePayload: {
      structured_plan: { steps: [] },
    },
  });

  assert.equal(result.id, 9);
  assert.deepEqual(
    calls.map((call) => call[0]),
    ['update', 'create', 'update'],
  );
  assert.equal(calls[1][1].name, 'Draft workflow');
  assert.equal(calls[2][1], 9);
});

test('saveWorkflowDraft keeps non-404 update errors', async () => {
  const api = {
    createWorkflow: async () => {
      throw new Error('create should not be called');
    },
    updateWorkflow: async () => {
      throw {
        isAxiosError: true,
        response: { status: 500 },
      };
    },
  };

  await assert.rejects(
    () => saveWorkflowDraft(api, {
      workflowId: 1,
      createPayload: {
        name: 'Draft workflow',
        natural_language_input: 'Create a document',
      },
      updatePayload: {
        structured_plan: { steps: [] },
      },
    }),
    (error) => error.response.status === 500,
  );
});

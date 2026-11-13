import assert from 'node:assert/strict';
import test from 'node:test';

import {
  describeRecordingStep,
  isMixedPlan,
  mergeRecordingDraft,
  planExecutors,
  recordingCommand,
} from '../src/lib/workflow-recording.ts';

test('mergeRecordingDraft appends steps and remaps dependencies', () => {
  const plan = {
    steps: [
      { step_id: 1, action: 'goto', depends_on: [] },
    ],
  };
  const draft = {
    executor_type: 'windows',
    steps: [
      {
        step_id: 1,
        action: 'launch_app',
        params: { path: 'notepad.exe' },
        depends_on: [],
      },
      {
        step_id: 2,
        action: 'type_text',
        params: { text: '周报' },
        depends_on: [1],
      },
    ],
  };

  const merged = mergeRecordingDraft(plan, draft);

  assert.equal(merged.steps?.length, 3);
  assert.equal(merged.steps?.[1].step_id, 2);
  assert.deepEqual(merged.steps?.[1].depends_on, []);
  assert.equal(merged.steps?.[1].executor, 'windows');
  assert.equal(merged.steps?.[2].step_id, 3);
  assert.deepEqual(merged.steps?.[2].depends_on, [2]);
});

test('mergeRecordingDraft replaces an empty plan and keeps capabilities', () => {
  const merged = mergeRecordingDraft(
    { steps: [], required_capabilities: ['uia'] },
    {
      executor_type: 'web',
      required_capabilities: ['browser'],
      steps: [
        {
          step_id: 'open',
          action: 'goto',
          params: { url: 'https://example.com' },
          depends_on: [],
        },
      ],
    },
  );

  assert.equal(merged.steps?.length, 1);
  assert.equal(merged.steps?.[0].step_id, 1);
  assert.equal(merged.steps?.[0].executor, 'web');
  assert.deepEqual(merged.required_capabilities, ['uia', 'browser']);
});

test('planExecutors detects hybrid plans', () => {
  const plan = {
    executor_type: 'web',
    steps: [
      { step_id: 1, executor: 'web', action: 'goto' },
      { step_id: 2, executor: 'windows', action: 'launch_app' },
    ],
  };

  assert.deepEqual(planExecutors(plan), ['web', 'windows']);
  assert.equal(isMixedPlan(plan), true);
  assert.equal(
    isMixedPlan({ executor_type: 'web', steps: [] }),
    false,
  );
});

test('describeRecordingStep prefers locator details', () => {
  const detail = describeRecordingStep({
    step_id: 1,
    action: 'click',
    locator: { strategy: 'role', value: 'button', name: '保存' },
    params: { timeout_ms: 5000 },
  });

  assert.match(detail, /role=button/);
  assert.match(detail, /timeout_ms=5000/);
});

test('recordingCommand includes the existing recording id', () => {
  const command = recordingCommand({
    baseUrl: 'http://localhost:8000/',
    token: 'jwt-token',
    recordingId: 'abc123',
    startUrl: 'https://example.com',
  });

  assert.match(command, /--recording-id abc123/);
  assert.match(command, /--api http:\/\/localhost:8000/);
  assert.match(command, /--token jwt-token/);
  assert.match(command, /--url "https:\/\/example.com"/);
});

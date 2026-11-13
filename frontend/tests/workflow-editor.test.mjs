import assert from 'node:assert/strict';
import test from 'node:test';

import {
  connectWorkflowNodes,
  disconnectWorkflowNodes,
  ensureExplicitDependencies,
  generateDesktopPreview,
  generatePlaywrightCode,
} from '../src/lib/workflow-editor.ts';

test('ensureExplicitDependencies preserves dependencies and fills defaults', () => {
  const plan = ensureExplicitDependencies({
    steps: [
      { step_id: 'open', action: 'navigate' },
      {
        step_id: 'extract',
        action: 'extract',
        depends_on: ['open'],
      },
      { step_id: 'notify', action: 'notify', depends_on: [] },
    ],
  });

  assert.deepEqual(plan.steps?.[0].depends_on, []);
  assert.deepEqual(plan.steps?.[1].depends_on, ['open']);
  assert.deepEqual(plan.steps?.[2].depends_on, []);
});

test('connectWorkflowNodes adds a dragged dependency', () => {
  const plan = {
    steps: [
      { step_id: 'one', action: 'navigate', depends_on: [] },
      { step_id: 'two', action: 'click', depends_on: [] },
      { step_id: 'three', action: 'extract', depends_on: ['two'] },
    ],
  };

  const result = connectWorkflowNodes(plan, 'step-one', 'step-three');

  assert.equal(result.error, undefined);
  assert.deepEqual(result.plan.steps?.[2].depends_on, ['two', 'one']);
});

test('connectWorkflowNodes rejects a dependency cycle', () => {
  const plan = {
    steps: [
      { step_id: 'one', action: 'navigate', depends_on: [] },
      { step_id: 'two', action: 'click', depends_on: ['one'] },
    ],
  };

  const result = connectWorkflowNodes(plan, 'step-two', 'step-one');

  assert.match(result.error ?? '', /循环/);
  assert.deepEqual(result.plan, plan);
});

test('disconnectWorkflowNodes removes the selected dependency', () => {
  const plan = {
    steps: [
      { step_id: 'one', action: 'navigate', depends_on: [] },
      { step_id: 'two', action: 'click', depends_on: ['one'] },
    ],
  };

  const result = disconnectWorkflowNodes(plan, 'step-one', 'step-two');

  assert.deepEqual(result.steps?.[1].depends_on, []);
});

test('trigger connections remain visible for non-first steps', () => {
  const plan = {
    steps: [
      { step_id: 'one', action: 'navigate', depends_on: [] },
      { step_id: 'two', action: 'click', depends_on: [] },
    ],
  };

  const connected = connectWorkflowNodes(
    plan,
    'trigger',
    'step-two',
  );
  assert.deepEqual(connected.plan.steps?.[1].depends_on, ['trigger']);

  const disconnected = disconnectWorkflowNodes(
    connected.plan,
    'trigger',
    'step-two',
  );
  assert.deepEqual(disconnected.steps?.[1].depends_on, []);
});

test('generatePlaywrightCode reflects edited step fields', () => {
  const code = generatePlaywrightCode({
    steps: [
      {
        step_id: 'open',
        action: 'navigate',
        params: { url: 'https://example.com/login' },
        depends_on: [],
      },
      {
        step_id: 'account',
        action: 'fill',
        selector: '#account',
        params: { value: 'demo-user' },
        depends_on: ['open'],
      },
      {
        step_id: 'submit',
        action: 'click',
        target: 'text=登录',
        depends_on: ['account'],
      },
    ],
  });

  assert.match(code, /page\.goto\("https:\/\/example\.com\/login"\)/);
  assert.match(code, /page\.fill\("#account", "demo-user"\)/);
  assert.match(code, /page\.click\("text=登录"\)/);
});

test('generateDesktopPreview renders desktop actions', () => {
  const code = generateDesktopPreview({
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
        params: { text: '周报', method: 'clipboard' },
        depends_on: [1],
      },
      {
        step_id: 3,
        action: 'click_element',
        params: { name: '保存' },
        depends_on: [2],
      },
    ],
  });

  assert.match(code, /# Step 1: 启动程序/);
  assert.match(code, /launch_app\(path="notepad\.exe"\)/);
  assert.match(code, /type_text\(text="周报"\)/);
  assert.match(code, /click_element\(name="保存"\)/);
});

test('generatePlaywrightCode marks non-web steps for hybrid routing', () => {
  const code = generatePlaywrightCode({
    executor_type: 'web',
    steps: [
      {
        step_id: 1,
        executor: 'windows',
        action: 'launch_app',
        params: { path: 'notepad.exe' },
        depends_on: [],
      },
    ],
  });

  assert.match(code, /hybrid executor: launch_app/);
  assert.match(code, /\(windows\)/);
});

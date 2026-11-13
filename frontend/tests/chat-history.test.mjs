import assert from 'node:assert/strict';
import test from 'node:test';

import {
  appendChatMessage,
  applyChatResponse,
  createChatSession,
  mergeChatSessions,
} from '../src/lib/chat-history.ts';

test('createChatSession starts an empty conversation', () => {
  const session = createChatSession(
    'session-1',
    '2026-09-16T10:00:00.000Z',
  );

  assert.equal(session.id, 'session-1');
  assert.equal(session.title, '新会话');
  assert.deepEqual(session.messages, []);
  assert.equal(session.updatedAt, '2026-09-16T10:00:00.000Z');
});

test('appendChatMessage uses the first user message as title', () => {
  const session = createChatSession(
    'session-1',
    '2026-09-16T10:00:00.000Z',
  );
  const updated = appendChatMessage(
    session,
    {
      id: 'message-1',
      role: 'user',
      content: '  每天同步订单数据  ',
      timestamp: '10:00',
    },
    '2026-09-16T10:01:00.000Z',
  );

  assert.equal(updated.title, '每天同步订单数据');
  assert.equal(updated.messages.length, 1);
  assert.equal(updated.messages[0].content, '每天同步订单数据');
  assert.equal(updated.updatedAt, '2026-09-16T10:01:00.000Z');
});

test('applyChatResponse stores the generated plan and code', () => {
  const session = createChatSession(
    'session-1',
    '2026-09-16T10:00:00.000Z',
  );
  const plan = {
    trigger: { type: 'manual', cron: null },
    steps: [{ step_id: 1, action: 'navigate' }],
  };
  const updated = applyChatResponse(
    session,
    {
      reply: '已生成',
      structured_plan: plan,
      generated_code: 'await page.goto("https://example.com")',
      needs_confirmation: true,
      suggestions: [],
    },
    '2026-09-16T10:02:00.000Z',
  );

  assert.deepEqual(updated.structuredPlan, plan);
  assert.equal(
    updated.generatedCode,
    'await page.goto("https://example.com")',
  );
  assert.equal(updated.needsConfirmation, true);
  assert.equal(updated.updatedAt, '2026-09-16T10:02:00.000Z');
});

test('mergeChatSessions keeps the newest copy of each session', () => {
  const local = [
    {
      ...createChatSession('local-only', '2026-09-16T10:00:00.000Z'),
      title: '本地会话',
    },
    {
      ...createChatSession('shared', '2026-09-16T10:03:00.000Z'),
      title: '较新的本地副本',
    },
  ];
  const remote = [
    {
      ...createChatSession('shared', '2026-09-16T10:02:00.000Z'),
      title: '较旧的服务端副本',
    },
    {
      ...createChatSession('remote-only', '2026-09-16T10:01:00.000Z'),
      title: '服务端会话',
    },
  ];

  const merged = mergeChatSessions(local, remote);

  assert.deepEqual(
    merged.map((session) => session.id).sort(),
    ['local-only', 'remote-only', 'shared'],
  );
  assert.equal(
    merged.find((session) => session.id === 'shared')?.title,
    '较新的本地副本',
  );
});

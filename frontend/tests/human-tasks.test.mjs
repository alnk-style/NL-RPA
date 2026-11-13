import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canResumeHumanTask,
  formatHumanTaskWaitingTime,
  humanTaskKindLabel,
  humanTaskStatusLabel,
  humanTaskStatusVariant,
  humanTaskVerificationMessage,
} from '../src/lib/human-tasks.ts';
import { queryKeys } from '../src/api/queryKeys.ts';

test('human task kinds and statuses have stable display labels', () => {
  assert.equal(humanTaskKindLabel('login_required'), '登录');
  assert.equal(humanTaskKindLabel('mfa_required'), '多因素验证');
  assert.equal(humanTaskKindLabel('captcha_required'), '验证码 / 扫码');
  assert.equal(humanTaskKindLabel('manual_confirmation'), '人工确认');
  assert.equal(humanTaskStatusLabel('pending'), '待处理');
  assert.equal(humanTaskStatusLabel('resolved'), '已解决');
  assert.equal(humanTaskStatusLabel('cancelled'), '已取消');
  assert.equal(humanTaskStatusLabel('expired'), '已过期');
  assert.equal(humanTaskStatusVariant('pending'), 'warning');
  assert.equal(humanTaskStatusVariant('resolved'), 'success');
  assert.equal(humanTaskStatusVariant('cancelled'), 'secondary');
  assert.equal(humanTaskStatusVariant('expired'), 'danger');
});

test('only pending unexpired tasks can resume and verification disables it', () => {
  assert.equal(canResumeHumanTask({ status: 'pending' }), true);
  assert.equal(
    canResumeHumanTask({ status: 'pending' }, { verifying: true }),
    false,
  );
  assert.equal(canResumeHumanTask({ status: 'resolved' }), false);
  assert.equal(
    canResumeHumanTask({
      status: 'pending',
      expires_at: '2026-11-11T00:00:00',
    }, { now: Date.parse('2026-11-11T00:01:00') }),
    false,
  );
});

test('waiting time is stable and does not invent values', () => {
  assert.equal(formatHumanTaskWaitingTime(null), '—');
  assert.equal(formatHumanTaskWaitingTime(0), '刚刚');
  assert.equal(formatHumanTaskWaitingTime(59), '不到 1 分钟');
  assert.equal(formatHumanTaskWaitingTime(180), '3 分钟');
  assert.equal(formatHumanTaskWaitingTime(7320), '2 小时 2 分钟');
  assert.equal(formatHumanTaskWaitingTime(172800), '2 天');
});

test('verification failures expose server reason instead of local success', () => {
  assert.equal(
    humanTaskVerificationMessage({
      response: {
        data: {
          detail: {
            code: 'verification_failed',
            reason: '仍未找到登录成功标志',
          },
        },
      },
    }),
    '仍未找到登录成功标志',
  );
  assert.equal(
    humanTaskVerificationMessage(null, '验证失败'),
    '验证失败',
  );
});

test('human task query key is stable and task scoped', () => {
  assert.deepEqual(queryKeys.humanTasks, ['human-tasks']);
  assert.deepEqual(queryKeys.humanTask(12), ['human-tasks', 12]);
});

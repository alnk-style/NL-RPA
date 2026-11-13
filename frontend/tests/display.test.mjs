import assert from 'node:assert/strict';
import test from 'node:test';

import { redactSensitiveText } from '../src/lib/display.ts';

test('redactSensitiveText masks tokens and authorization values', () => {
  const text = redactSensitiveText(
    'failed token=abc&password=hunter2 Authorization: Bearer secret.jwt',
  );

  assert.doesNotMatch(text, /abc|hunter2|secret\.jwt/);
  assert.match(text, /token=\*\*\*/);
  assert.match(text, /password=\*\*\*/);
});


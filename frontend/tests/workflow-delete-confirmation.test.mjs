import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('workflow deletion uses an in-app confirmation dialog', () => {
  const source = readFileSync(
    new URL('../src/pages/WorkflowsPage.tsx', import.meta.url),
    'utf8',
  );

  assert.doesNotMatch(source, /window\.confirm/);
  assert.doesNotMatch(source, /window\.prompt/);
  assert.match(source, /deleteTarget/);
  assert.match(source, /deleteMutation/);
});

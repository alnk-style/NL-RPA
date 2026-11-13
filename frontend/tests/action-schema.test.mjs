import assert from 'node:assert/strict';
import test from 'node:test';

import { schemaToStudioFields } from '../src/lib/action-schema.ts';

test('action schema maps basic and specialized controls', () => {
  const fields = schemaToStudioFields({
    type: 'object',
    required: ['url', 'body', 'mode'],
    properties: {
      url: { type: 'string' },
      body: { type: 'string', format: 'multiline' },
      count: { type: 'integer' },
      enabled: { type: 'boolean' },
      mode: { type: 'string', enum: ['a', 'b'] },
      tags: { type: 'array', items: { type: 'string' } },
      metadata: { type: 'object' },
      credential_key: { type: 'string', format: 'credential' },
      value: { type: 'string', format: 'variable' },
      condition: { type: 'object', format: 'condition' },
    },
  });
  assert.deepEqual(
    fields.map((field) => [field.key, field.control]),
    [
      ['url', 'text'],
      ['body', 'textarea'],
      ['count', 'number'],
      ['enabled', 'switch'],
      ['mode', 'select'],
      ['tags', 'array'],
      ['metadata', 'object'],
      ['credential_key', 'credential'],
      ['value', 'variable'],
      ['condition', 'condition'],
    ],
  );
});

test('unsupported schema falls back to scoped JSON', () => {
  const [field] = schemaToStudioFields({
    type: 'object',
    properties: {
      custom: { type: 'string', oneOf: [{ const: 'x' }] },
    },
  });
  assert.equal(field.control, 'json');
});

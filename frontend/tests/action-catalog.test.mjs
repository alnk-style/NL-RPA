import assert from 'node:assert/strict';
import test from 'node:test';

import {
  collectVariableHints,
  executorUsesCatalogForm,
  schemaToActionFields,
} from '../src/lib/action-catalog.ts';

test('schemaToActionFields maps basic data action controls', () => {
  const fields = schemaToActionFields({
    type: 'object',
    additionalProperties: false,
    required: ['values', 'operator'],
    properties: {
      values: { type: 'array' },
      operator: { type: 'string', enum: ['eq', 'gt'] },
      field: { type: 'string', title: '字段路径' },
      allow_empty: { type: 'boolean', title: '允许空结果' },
      count: { type: 'integer' },
      mapping: { type: 'object' },
    },
  });

  assert.deepEqual(
    fields.map((field) => [field.key, field.control, field.required]),
    [
      ['values', 'json', true],
      ['operator', 'enum', true],
      ['field', 'text', false],
      ['allow_empty', 'boolean', false],
      ['count', 'number', false],
      ['mapping', 'json', false],
    ],
  );
  assert.deepEqual(fields[1].options, ['eq', 'gt']);
});

test('collectVariableHints includes declarations, result variables and refs', () => {
  const hints = collectVariableHints({
    variables: [{ name: 'transactions', default: [] }],
    steps: [
      {
        action: 'filter',
        params: { values: '${transactions.rows}' },
        result_var: 'large_transactions',
      },
      {
        action: 'write_xlsx',
        params: { values: '${large_transactions}', path: '${report_file}' },
        result_var: 'report_file',
      },
    ],
  });

  assert.deepEqual(
    hints.map((hint) => hint.name),
    [
      'transactions',
      'transactions.rows',
      'large_transactions',
      'report_file',
    ],
  );
  assert.equal(hints[1].expression, '${transactions.rows}');
});

test('email executor uses the shared catalog form', () => {
  assert.equal(executorUsesCatalogForm('email'), true);
  const fields = schemaToActionFields({
    type: 'object',
    required: ['to', 'subject', 'body'],
    properties: {
      to: { type: 'array', items: { type: 'string' } },
      cc: { type: 'array', items: { type: 'string' } },
      bcc: { type: 'array', items: { type: 'string' } },
      subject: { type: 'string' },
      body: { type: 'string' },
      attachments: { type: 'array', items: { type: 'string' } },
      credential_key: { type: 'string' },
      required_delivery: { type: 'boolean' },
    },
  });
  assert.deepEqual(
    fields.map((field) => [field.key, field.control, field.required]),
    [
      ['to', 'json', true],
      ['cc', 'json', false],
      ['bcc', 'json', false],
      ['subject', 'text', true],
      ['body', 'text', true],
      ['attachments', 'json', false],
      ['credential_key', 'text', false],
      ['required_delivery', 'boolean', false],
    ],
  );
});

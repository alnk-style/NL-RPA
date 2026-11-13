import assert from 'node:assert/strict';
import test from 'node:test';

import { dataPreviewModel } from '../src/lib/data-preview.ts';
import { dataPreviewCounts } from '../src/lib/data-preview.ts';

test('builds typed columns and stable row statistics', () => {
  const model = dataPreviewModel([
    { id: 1, status: 'paid', amount: 1200 },
    { id: 2, status: null, amount: '' },
    { id: 2, status: null, amount: '' },
  ]);

  assert.deepEqual(
    model.columns.map((column) => [column.key, column.type]),
    [
      ['id', 'number'],
      ['status', 'string | object'],
      ['amount', 'number | string'],
    ],
  );
  assert.equal(model.rowCount, 3);
  assert.equal(model.previewRowCount, 3);
  assert.equal(model.truncated, false);
  assert.equal(model.emptyCount, 4);
  assert.equal(model.duplicateCount, 1);
});

test('an empty array is preview-empty without inferring business success', () => {
  const model = dataPreviewModel([]);

  assert.deepEqual(model, {
    rows: [],
    columns: [],
    rowCount: 0,
    previewRowCount: 0,
    truncated: false,
    emptyCount: 0,
    duplicateCount: 0,
  });
  assert.equal('success' in model, false);
  assert.equal('allow_empty' in model, false);
});

test('limits large previews while retaining the full row count', () => {
  const model = dataPreviewModel(
    Array.from({ length: 75 }, (_, index) => ({
      index,
      nested: { value: index },
    })),
  );

  assert.equal(model.rowCount, 75);
  assert.equal(model.previewRowCount, 50);
  assert.equal(model.rows.length, 50);
  assert.equal(model.truncated, true);
  assert.equal(model.columns[1].type, 'object');
});

test('reads filter counts from backend step verification only', () => {
  assert.deepEqual(
    dataPreviewCounts([
      { status: 'success' },
      {
        status: 'success',
        verification: { input_count: 8, output_count: 3 },
      },
    ]),
    { inputCount: 8, outputCount: 3 },
  );
  assert.equal(dataPreviewCounts([{ status: 'failed' }]), null);
});

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildFailureCategoryRows,
  buildMetricCards,
  failureCategoryLabel,
  formatMetricValue,
  formatPercent,
  formatSeconds,
  summarizeExecutionSteps,
} from '../src/lib/metrics.ts';

function overviewFixture() {
  return {
    enabled: true,
    generated_at: '2026-10-02T12:00:00',
    range: { preset: '7d', start: null, end: '2026-10-02T12:00:00' },
    dimensions: { workflow_id: null, executor: null, user_id: 1 },
    execution_count: 4,
    metrics: {
      first_generation_success_rate: {
        value: 0.75,
        unit: 'ratio',
        numerator: 3,
        denominator: 4,
      },
      locator_failure_rate: {
        value: 0.2,
        unit: 'ratio',
        locator_failures: 2,
        step_count: 10,
      },
      auto_repair_success_rate: {
        value: 0.5,
        unit: 'ratio',
        repairs_attempted: 4,
        repairs_succeeded: 2,
      },
      manual_interventions_per_100_executions: {
        value: 25,
        unit: 'per_100_executions',
        interventions: 1,
      },
      workflow_creation_duration_seconds: {
        value: 42.5,
        unit: 'seconds',
        samples: 3,
      },
      full_rerun_count: {
        value: 2,
        unit: 'executions',
        execution_count: 4,
      },
      execution_duration_delta_ratio: {
        value: 1.35,
        unit: 'ratio',
        delta_percent: 35,
      },
    },
    failure_categories: {
      locator_not_found: { count: 2, rate: 1, share_percent: 100 },
      timeout: { count: 0, rate: 0, share_percent: 0 },
    },
  };
}

test('formatPercent and formatSeconds render stable labels', () => {
  assert.equal(formatPercent(0.7543), '75.4%');
  assert.equal(formatPercent(null), '—');
  assert.equal(formatSeconds(0.42), '420 ms');
  assert.equal(formatSeconds(12.34), '12.3 s');
  assert.equal(formatSeconds(75), '1m15s');
  assert.equal(formatSeconds(null), '—');
});

test('formatMetricValue uses the unit', () => {
  assert.equal(
    formatMetricValue({ value: 0.6, unit: 'ratio' }),
    '60.0%',
  );
  assert.equal(
    formatMetricValue({ value: 30, unit: 'seconds' }),
    '30.0 s',
  );
  assert.equal(
    formatMetricValue({ value: 12.5, unit: 'per_100_executions' }),
    '12.5',
  );
  assert.equal(
    formatMetricValue({ value: 4, unit: 'executions' }),
    '4',
  );
  assert.equal(formatMetricValue(undefined), '—');
});

test('buildMetricCards returns seven cards with tones', () => {
  const cards = buildMetricCards(overviewFixture());
  assert.equal(cards.length, 7);
  const byKey = Object.fromEntries(cards.map((card) => [card.key, card]));
  assert.equal(byKey.first_generation_success_rate.value, '75.0%');
  assert.equal(byKey.locator_failure_rate.tone, 'warning');
  assert.equal(byKey.execution_duration_delta_ratio.tone, 'warning');
  assert.equal(byKey.execution_duration_delta_ratio.detail, '+35.0%');
  assert.equal(
    byKey.manual_interventions_per_100_executions.value,
    '25.0',
  );
});

test('buildMetricCards handles missing overview', () => {
  assert.deepEqual(buildMetricCards(null), []);
});

test('buildFailureCategoryRows filters zero counts and sorts', () => {
  const rows = buildFailureCategoryRows({
    timeout: { count: 1, rate: 0.25, share_percent: 25 },
    locator_not_found: { count: 3, rate: 0.75, share_percent: 75 },
    unknown: { count: 0, rate: 0, share_percent: 0 },
  });
  assert.deepEqual(
    rows.map((row) => [row.category, row.count]),
    [
      ['locator_not_found', 3],
      ['timeout', 1],
    ],
  );
  assert.equal(failureCategoryLabel('auth_required'), '需要登录');
  assert.equal(failureCategoryLabel('custom_category'), 'custom_category');
});

test('summarizeExecutionSteps counts failures and repairs', () => {
  const summary = summarizeExecutionSteps([
    { step_index: 0, status: 'success' },
    {
      step_index: 1,
      status: 'failed',
      failure_category: 'locator_not_found',
      attempts: 2,
    },
    {
      step_index: 2,
      status: 'repaired',
      failure_category: 'element_not_visible',
      repaired_step: { action: 'click' },
    },
    { type: 'checkpoint', status: 'awaiting_user' },
  ]);
  assert.equal(summary.stepCount, 3);
  assert.equal(summary.failedSteps, 1);
  assert.equal(summary.locatorFailures, 2);
  assert.equal(summary.repairsAttempted, 2);
  assert.equal(summary.repairsSucceeded, 1);
  assert.deepEqual(
    summary.failureCategories.map((row) => row.category),
    ['locator_not_found'],
  );
});

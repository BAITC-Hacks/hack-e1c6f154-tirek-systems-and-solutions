'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Monitor, evaluate, validatePolicy } = require('./monitor-core.js');
const policy = { reference_stock: 100, reference_kind: 'manual', reference_id: 'synthetic', source_kind: 'synthetic', policy_version: 'p1',
  thresholds: { warning_pct: 40, high_pct: 30, critical_pct: 20, hysteresis_pp: 3 } };
const clock = () => '2026-09-23T07:00:00.000Z';
const create = (extras = {}) => new Monitor({ sku: 'demo', warehouse_id: 'demo', on_hand: 60, reserved: 0, policy, ...extras }, clock);
function event(m, onHand, reserved = 0, id = 'e' + m.snapshot().revision) {
  return { event_id: id, expected_revision: m.snapshot().revision, sku: 'demo', warehouse_id: 'demo', unit: m.snapshot().unit,
    occurred_at: clock(), event_type: 'adjustment', on_hand: onHand, reserved, source_reference: 'test' };
}
test('inclusive boundaries and zero', () => {
  for (const [qty, expected] of [[41,'normal'],[40,'warning'],[30,'high'],[20,'critical'],[0,'stockout']]) {
    assert.equal(evaluate(qty, 0, policy).active_level, expected);
  }
});
test('sale escalates at once; no duplicate signal inside level', () => {
  const m = create();
  assert.equal(m.apply(event(m, 39)).transition.to_level, 'warning');
  assert.equal(m.apply(event(m, 38)).transition, null);
  assert.equal(m.apply(event(m, 29)).transition.to_level, 'high');
  assert.equal(m.apply(event(m, 19)).transition.to_level, 'critical');
});
test('multi-level jump creates one transition', () => {
  const m = create(); const result = m.apply(event(m, 15));
  assert.equal(result.transition.from_level, 'normal'); assert.equal(result.transition.to_level, 'critical');
});
test('recovery hysteresis applies only to recovery', () => {
  const m = create(); m.apply(event(m, 19));
  const held = m.apply(event(m, 22));
  assert.equal(held.state.observed_level, 'high'); assert.equal(held.state.active_level, 'critical');
  assert.equal(m.apply(event(m, 23)).state.active_level, 'high');
  assert.equal(m.apply(event(m, 32)).state.active_level, 'high');
  assert.equal(m.apply(event(m, 33)).state.active_level, 'warning');
  assert.equal(m.apply(event(m, 42)).state.active_level, 'warning');
  assert.equal(m.apply(event(m, 43)).state.active_level, 'normal');
  assert.equal(m.apply(event(m, 19)).state.active_level, 'critical');
});
test('stockout recovers immediately to critical with positive stock', () => {
  const m = create(); m.apply(event(m, 0));
  assert.equal(m.apply(event(m, 1)).state.active_level, 'critical');
});
test('replayed event is deduplicated before stale revision, without rolling back state', () => {
  const m = create(); const first = event(m, 39); m.apply(first); m.apply(event(m, 29));
  const repeated = m.apply(first);
  assert.equal(repeated.deduplicated, true); assert.equal(repeated.state.revision, 2);
  assert.equal(m.snapshot().revision, 3); assert.equal(m.snapshot().free_stock, 29);
  assert.throws(() => m.apply({ ...first, on_hand: 38 }), { code: 'IDEMPOTENCY_CONFLICT' });
});
test('stale or invalid events do not mutate state', () => {
  const m = create(); const before = m.snapshot();
  assert.throws(() => m.apply({ ...event(m, 30), expected_revision: 0 }), { code: 'STALE_REVISION' });
  assert.throws(() => m.apply(event(m, 5, 10)), { code: 'INVALID_INVENTORY' });
  assert.throws(() => m.apply(event(m, 2.5)), { code: 'INVALID_UNIT' });
  assert.deepEqual(m.snapshot(), before);
});
test('reserved shipment does not consume free inventory twice', () => {
  const m = create(); m.apply(event(m, 60, 30));
  assert.equal(m.snapshot().free_stock, 30);
  assert.equal(m.apply({ ...event(m, 30, 0), event_type: 'sale' }).state.free_stock, 30);
});
test('unknown base and invalid inventory are not normal; known zero remains stockout', () => {
  const missing = { ...policy, reference_stock: null, reference_kind: 'unavailable', reference_id: null };
  assert.equal(evaluate(10, 0, missing).active_level, 'unknown');
  assert.equal(evaluate(0, 0, missing).active_level, 'stockout');
  assert.equal(evaluate(10, 11, policy).active_level, 'unknown');
  assert.equal(evaluate(null, 0, policy).remaining_pct, null);
  assert.throws(() => validatePolicy({ ...policy, reference_stock: 0 }), { code: 'INVALID_POLICY' });
});
test('policy update is versioned and distinguished from a sale', () => {
  const m = create();
  const result = m.updatePolicy({ ...policy, reference_stock: 300, policy_version: 'p2' }, 1, 'new target');
  assert.equal(result.state.active_level, 'critical'); assert.equal(result.state.revision, 2);
  assert.equal(result.transition.kind, 'policy_changed'); assert.equal(m.snapshot().on_hand, 60);
  assert.throws(() => m.updatePolicy(policy, 1, 'old'), { code: 'STALE_REVISION' });
});
test('individual thresholds and decimal quantities', () => {
  const individual = { ...policy, thresholds: { ...policy.thresholds, warning_pct: 60, high_pct: 45, critical_pct: 25 } };
  assert.equal(evaluate(50, 0, individual).active_level, 'warning');
  assert.equal(evaluate(50, 0, policy).active_level, 'normal');
  assert.equal(evaluate(0.3, 0.1, { ...policy, reference_stock: 1 }).active_level, 'critical');
  assert.equal(evaluate(150, 0, policy).remaining_pct, 150);
});
test('invalid policy is rejected atomically', () => {
  const m = create(); const before = m.snapshot();
  assert.throws(() => m.updatePolicy({ ...policy, policy_version: 'p2', thresholds: { ...policy.thresholds, critical_pct: 80 } }, 1, 'invalid'), { code: 'INVALID_POLICY' });
  assert.deepEqual(m.snapshot(), before);
});

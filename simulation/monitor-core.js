/* Synthetic, in-memory reference implementation; no HTTP server or ML is implied. */
(function (root) {
  'use strict';
  const levels = ['normal', 'warning', 'high', 'critical', 'stockout'];
  const clone = value => structuredClone(value);
  const valid = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const rounded = value => Math.round(value * 1e6) / 1e6;
  const atLeast = (value, threshold) => value >= threshold - 1e-9;
  function fail(code, message) { const error = new Error(message); error.code = code; throw error; }
  function validatePolicy(policy) {
    const t = policy.thresholds;
    if (!t || ![t.warning_pct, t.high_pct, t.critical_pct, t.hysteresis_pp].every(valid) ||
        !(0 < t.critical_pct && t.critical_pct < t.high_pct && t.high_pct < t.warning_pct && t.warning_pct < 100) ||
        t.hysteresis_pp > 20 || t.warning_pct + t.hysteresis_pp >= 100) {
      fail('INVALID_POLICY', 'Нужно 0 < критический < высокий < внимание < 100; гистерезис 0–20 п.п., верхняя граница восстановления < 100%.');
    }
    if (policy.reference_stock !== null && (!valid(policy.reference_stock) || policy.reference_stock === 0)) {
      fail('INVALID_POLICY', 'Целевой запас должен быть больше нуля или явно неизвестен.');
    }
    if ((policy.reference_stock === null) !== (policy.reference_kind === 'unavailable')) {
      fail('INVALID_POLICY', 'Неизвестный ориентир требует reference_kind=unavailable.');
    }
    if (!policy.policy_version || !['manual', 'calculation', 'unavailable'].includes(policy.reference_kind) ||
        !['manual', 'observed', 'synthetic'].includes(policy.source_kind)) fail('INVALID_POLICY', 'Укажите версию и источник политики.');
  }
  function evaluate(onHand, reserved, policy, previous = 'unknown') {
    if (!valid(onHand) || !valid(reserved) || reserved > onHand) {
      return { free_stock: null, remaining_pct: null, observed_level: 'unknown', active_level: 'unknown', issues: ['INVALID_OR_MISSING_INVENTORY'] };
    }
    const free = rounded(onHand - reserved);
    const pct = policy.reference_stock === null ? null : 100 * free / policy.reference_stock;
    if (free === 0) return { free_stock: 0, remaining_pct: pct, observed_level: 'stockout', active_level: 'stockout', issues: pct === null ? ['MISSING_REFERENCE_STOCK'] : [] };
    if (pct === null) return { free_stock: free, remaining_pct: null, observed_level: 'unknown', active_level: 'unknown', issues: ['MISSING_REFERENCE_STOCK'] };
    const t = policy.thresholds;
    const observed = pct <= t.critical_pct + 1e-9 ? 'critical' : pct <= t.high_pct + 1e-9 ? 'high' : pct <= t.warning_pct + 1e-9 ? 'warning' : 'normal';
    let active = observed;
    const priorRank = levels.indexOf(previous), rawRank = levels.indexOf(observed);
    if (priorRank > rawRank && priorRank < 4) {
      let rank = priorRank;
      while (rank > rawRank && atLeast(pct, t[levels[rank] + '_pct'] + t.hysteresis_pp)) rank--;
      active = levels[rank];
    }
    return { free_stock: free, remaining_pct: pct, observed_level: observed, active_level: active, issues: [] };
  }
  function canonical(value) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
    }
    return JSON.stringify(value);
  }
  class Monitor {
    constructor({ sku, warehouse_id, unit = 'шт', mode = 'scenario', on_hand, reserved = 0, policy }, clock = () => new Date().toISOString()) {
      validatePolicy(policy);
      if (mode !== 'scenario') fail('SIMULATION_ONLY', 'Этот симулятор работает только в scenario.');
      this.clock = clock;
      this.events = new Map();
      this.state = { sku, warehouse_id, unit, mode, revision: 1, on_hand, reserved, policy: clone(policy), evaluated_at: clock(), ...evaluate(on_hand, reserved, policy) };
    }
    snapshot() { return clone(this.state); }
    transition(previous, cause, eventId) {
      const current = this.state.active_level;
      if (previous === current && cause !== 'policy_changed') return null;
      const kind = cause === 'policy_changed' ? 'policy_changed' :
        (previous === 'unknown' || current === 'unknown') ? 'data_quality' :
        levels.indexOf(current) > levels.indexOf(previous) ? 'escalation' : 'recovery';
      return { transition_id: `${this.state.sku}:${this.state.revision}`, cause, event_id: eventId,
        from_level: previous, to_level: current, kind, created_at: this.state.evaluated_at };
    }
    apply(event) {
      if (typeof event.event_id !== 'string' || !event.event_id.trim()) fail('INVALID_EVENT', 'Нужен event_id.');
      const signature = canonical(event);
      if (this.events.has(event.event_id)) {
        const stored = this.events.get(event.event_id);
        if (stored.signature !== signature) fail('IDEMPOTENCY_CONFLICT', 'Этот event_id уже использован для другого события.');
        return { ...clone(stored.result), deduplicated: true };
      }
      if (event.expected_revision !== this.state.revision) fail('STALE_REVISION', 'Остаток уже изменился. Получите новую ревизию.');
      if (event.sku !== this.state.sku || event.warehouse_id !== this.state.warehouse_id || event.unit !== this.state.unit) fail('INVALID_EVENT', 'Товар, склад или единица не совпадают.');
      if (!['sale', 'receipt', 'reservation', 'release', 'adjustment'].includes(event.event_type) ||
          !Number.isFinite(Date.parse(event.occurred_at)) || !event.source_reference) fail('INVALID_EVENT', 'Неверные тип, время или источник события.');
      if (!valid(event.on_hand) || !valid(event.reserved) || event.reserved > event.on_hand) fail('INVALID_INVENTORY', 'Остаток и резерв должны быть неотрицательны, резерв не больше остатка.');
      if (this.state.unit === 'шт' && (!Number.isInteger(event.on_hand) || !Number.isInteger(event.reserved))) fail('INVALID_UNIT', 'Штучный товар учитывается целыми единицами.');
      const previous = this.state.active_level;
      Object.assign(this.state, { on_hand: event.on_hand, reserved: event.reserved, revision: this.state.revision + 1,
        evaluated_at: this.clock() }, evaluate(event.on_hand, event.reserved, this.state.policy, previous));
      const result = { state: this.snapshot(), transition: this.transition(previous, 'inventory_event', event.event_id),
        deduplicated: false, recalculation_requested: false };
      this.events.set(event.event_id, { signature, result: clone(result) });
      return result;
    }
    updatePolicy(policy, expectedRevision, reason) {
      if (expectedRevision !== this.state.revision) fail('STALE_REVISION', 'Политика или остаток уже изменились.');
      if (typeof reason !== 'string' || !reason.trim()) fail('REASON_REQUIRED', 'Укажите причину изменения политики.');
      validatePolicy(policy);
      if (policy.policy_version === this.state.policy.policy_version) fail('POLICY_VERSION_CONFLICT', 'Нужна новая версия политики.');
      const previous = this.state.active_level;
      Object.assign(this.state, { policy: clone(policy), revision: this.state.revision + 1, evaluated_at: this.clock() },
        evaluate(this.state.on_hand, this.state.reserved, policy));
      return { state: this.snapshot(), transition: this.transition(previous, 'policy_changed', null),
        deduplicated: false, recalculation_requested: false };
    }
  }
  const api = { Monitor, evaluate, validatePolicy };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TirekMonitor = api;
})(globalThis);

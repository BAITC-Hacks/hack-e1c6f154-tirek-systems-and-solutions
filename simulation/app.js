'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const labels = { normal: 'Норма', warning: 'Внимание', high: 'Высокий', critical: 'Критический', stockout: 'Нет доступного товара', unknown: 'Недостаточно данных' };
  const profiles = {
    standard: { sku: 'DEMO-A', stock: 60, target: 100, thresholds: [40, 30, 20] },
    service: { sku: 'DEMO-B', stock: 15, target: 20, thresholds: [60, 40, 25] },
    volume: { sku: 'DEMO-C', stock: 240, target: 400, thresholds: [40, 30, 20] }
  };
  const descriptions = {
    normal: ['Запас выше порогов внимания', 'Сигнал появится при пересечении порога. Товары в пути не увеличивают доступный остаток.'],
    warning: ['Обратите внимание на пополнение', 'Порог внимания пройден. Проверьте ожидаемый спрос и ближайшие поступления.'],
    high: ['Приоритет пополнения повышен', 'Проверьте количество к закупке и срок поставки. Заказ требует утверждения.'],
    critical: ['Критический уровень запаса', 'Нужно срочно проверить пополнение и возможность ускорить поставку.'],
    stockout: ['Доступного товара нет', 'Проверьте резервы и поставки. Процент не заменяет оценку упущенного спроса.'],
    unknown: ['Нужно уточнить данные', 'Без известного целевого запаса процент не рассчитывается. Укажите ориентир в настройках.']
  };
  let monitor, history = [], lastEvent = null, busy = false, sequence = 0;
  const format = value => value === null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value);
  const setError = message => { $('error').textContent = message || ''; $('error').hidden = !message; };
  function render() {
    const s = monitor.snapshot(), t = s.policy.thresholds;
    $('free').textContent = format(s.free_stock); $('on-hand').textContent = format(s.on_hand);
    $('reserved').textContent = format(s.reserved); $('revision').textContent = s.revision;
    $('percent').textContent = s.remaining_pct === null ? '—' : format(s.remaining_pct) + '%';
    $('reference').textContent = s.policy.reference_stock === null ? 'неизвестен' : format(s.policy.reference_stock) + ' шт.';
    $('policy-version').textContent = s.policy.policy_version;
    $('level').textContent = labels[s.active_level]; $('level').className = 'status ' + s.active_level;
    const scale = Math.max(100, s.remaining_pct || 0);
    $('meter-fill').style.width = s.remaining_pct === null ? '0%' : (100 * s.remaining_pct / scale) + '%';
    $('meter-fill').style.backgroundColor = ['critical','stockout','high','warning'].includes(s.active_level) ? 'var(--' + (s.active_level === 'stockout' ? 'critical' : s.active_level) + ')' : 'var(--accent)';
    $('meter').setAttribute('aria-valuemax', String(scale));
    if (s.remaining_pct === null) $('meter').removeAttribute('aria-valuenow');
    else $('meter').setAttribute('aria-valuenow', String(s.remaining_pct));
    $('meter').setAttribute('aria-valuetext', s.remaining_pct === null ? 'Процент неизвестен' : format(s.remaining_pct) + '%; ' + labels[s.active_level]);
    for (const key of ['critical','high','warning']) {
      $('mark-' + key).style.left = (100 * t[key + '_pct'] / scale) + '%';
      $('mark-' + key).hidden = s.remaining_pct === null;
      $('legend-' + key).textContent = '≤ ' + format(t[key + '_pct']) + '% · ' + labels[key].toLowerCase();
    }
    $('notice').className = 'notice ' + s.active_level;
    $('notice-title').textContent = descriptions[s.active_level][0];
    $('notice-text').textContent = descriptions[s.active_level][1];
    const held = s.active_level !== s.observed_level;
    $('hold-note').hidden = !held;
    if (held) $('hold-note').textContent = 'Сигнал сохраняется до ' + format(t[s.active_level + '_pct'] + t.hysteresis_pp) + '%: защита от частых переключений около порога.';
    $('replay').disabled = busy || !lastEvent;
    $('event-count').textContent = history.length + ' записей';
    $('empty-journal').hidden = history.length > 0;
    $('journal-body').replaceChildren();
    for (const entry of history.slice(-12).reverse()) {
      const row = document.createElement('tr');
      [entry.label, format(entry.state.free_stock) + ' шт.', entry.state.remaining_pct === null ? '—' : format(entry.state.remaining_pct) + '%', labels[entry.state.active_level], entry.reaction].forEach((value, i) => {
        const cell = document.createElement('td');
        if (i === 3) { const badge = document.createElement('span'); badge.className = 'status ' + entry.state.active_level; badge.textContent = value; cell.append(badge); }
        else cell.textContent = value;
        row.append(cell);
      });
      $('journal-body').append(row);
    }
  }
  function syncSettings() {
    const p = monitor.snapshot().policy;
    $('target-input').value = p.reference_stock === null ? '' : p.reference_stock;
    for (const name of ['warning', 'high', 'critical']) $(name + '-input').value = p.thresholds[name + '_pct'];
    $('hysteresis-input').value = p.thresholds.hysteresis_pp;
  }
  function reset() {
    const p = profiles[$('profile').value];
    monitor = new TirekMonitor.Monitor({ sku: p.sku, warehouse_id: 'demo-almaty', on_hand: p.stock, policy: {
      reference_stock: p.target, reference_kind: 'manual', reference_id: 'simulation:target', source_kind: 'synthetic', policy_version: 'policy-1',
      thresholds: { warning_pct: p.thresholds[0], high_pct: p.thresholds[1], critical_pct: p.thresholds[2], hysteresis_pp: 3 }
    }});
    history = []; lastEvent = null; setError(''); syncSettings(); render();
  }
  function record(result, label) {
    const reaction = result.deduplicated ? 'Повтор: без списания и сигнала' : result.transition ?
      { escalation: 'Новый сигнал', recovery: 'Восстановление', data_quality: 'Изменение качества данных', policy_changed: 'Новая политика' }[result.transition.kind] : 'Уровень без изменений';
    // A replay contains its original snapshot; never roll the visible state back.
    history.push({ label, state: monitor.snapshot(), reaction }); render();
    if (result.deduplicated) { $('notice-title').textContent = 'Повтор события распознан'; $('notice-text').textContent = 'Остаток и текущая ревизия сохранены. Повторного списания и нового сигнала нет.'; }
  }
  function applyAction(action, quantity) {
    if (!Number.isInteger(quantity) || quantity <= 0) throw new Error('Введите положительное целое количество.');
    const s = monitor.snapshot(); let onHand = s.on_hand, reserved = s.reserved;
    if (action === 'sale') onHand -= quantity;
    if (action === 'receipt') onHand += quantity;
    if (action === 'reservation') reserved += quantity;
    if (action === 'release') reserved -= quantity;
    if (action === 'ship') { onHand -= quantity; reserved -= quantity; }
    const event = { event_id: 'sim-' + (++sequence), expected_revision: s.revision, sku: s.sku, warehouse_id: s.warehouse_id, unit: s.unit,
      occurred_at: new Date().toISOString(), event_type: action === 'ship' ? 'sale' : action, on_hand: onHand, reserved, source_reference: 'synthetic:browser' };
    const result = monitor.apply(event); lastEvent = event;
    const names = { sale: 'Выдача', receipt: 'Приход', reservation: 'Резерв', release: 'Снятие резерва', ship: 'Отгрузка резерва' };
    record(result, names[action] + ' · ' + quantity + ' шт.');
  }
  for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click', () => {
    setError(''); try { applyAction(button.dataset.action, Number($('quantity').value)); } catch (error) { setError(error.message); }
  });
  $('profile').addEventListener('change', reset); $('reset').addEventListener('click', reset);
  $('replay').addEventListener('click', () => { setError(''); try { record(monitor.apply(lastEvent), 'Повтор того же event_id'); } catch (error) { setError(error.message); } });
  $('policy-form').addEventListener('submit', event => {
    event.preventDefault(); setError('');
    const s = monitor.snapshot(), target = $('target-input').value === '' ? null : Number($('target-input').value);
    const policy = { ...s.policy, reference_stock: target, reference_kind: target === null ? 'unavailable' : 'manual',
      reference_id: target === null ? null : 'simulation:manual', policy_version: 'policy-' + (s.revision + 1), thresholds: {
        warning_pct: Number($('warning-input').value), high_pct: Number($('high-input').value), critical_pct: Number($('critical-input').value), hysteresis_pp: Number($('hysteresis-input').value) } };
    try { record(monitor.updatePolicy(policy, s.revision, $('reason-input').value), 'Изменение политики'); } catch (error) { setError(error.message); }
  });
  $('demo').addEventListener('click', async () => {
    busy = true; $('profile').value = 'standard'; reset();
    const controls = [...document.querySelectorAll('button, input, select')]; controls.forEach(el => { el.disabled = true; });
    $('demo').textContent = 'Выдаём товар…';
    try { for (const quantity of [21, 10, 10]) { await new Promise(resolve => setTimeout(resolve, 650)); applyAction('sale', quantity); } }
    catch (error) { setError(error.message); }
    finally { busy = false; controls.forEach(el => { el.disabled = false; }); $('demo').textContent = 'Демо: 60 → 39 → 29 → 19'; render(); }
  });
  reset();
})();

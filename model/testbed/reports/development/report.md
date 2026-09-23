# Testbed v2 — development

Synthetic; adapter `model.testbed.baseline:forecast`; seeds [101, 202, 303].

WAPE по реализованному скрытому регулярному спросу: **25.36%**.
WAPE относительно математического ожидания: **24.92%**.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **54/54**, статус `reference_only`.
Парные MH4 (прогноз и количество): **48/48**.
Строк: 288; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|
| stable/101 | 8 | 2.38% | 2.31% | 0 |
| stable/202 | 8 | 3.23% | 2.03% | 0 |
| stable/303 | 8 | 2.40% | 2.11% | 0 |
| seasonal_growth/101 | 8 | 18.41% | 19.38% | 0 |
| seasonal_growth/202 | 8 | 14.70% | 15.13% | 0 |
| seasonal_growth/303 | 8 | 17.86% | 18.52% | 0 |
| intermittent/101 | 8 | 105.93% | 50.28% | 0 |
| intermittent/202 | 8 | 128.96% | 44.61% | 0 |
| intermittent/303 | 8 | 138.59% | 68.50% | 0 |
| new_product/101 | 8 | 10.08% | 9.53% | 0 |
| new_product/202 | 8 | 9.96% | 8.74% | 0 |
| new_product/303 | 8 | 8.22% | 9.82% | 0 |
| full_stockout/101 | 8 | 2.32% | 1.90% | 0 |
| full_stockout/202 | 8 | 3.63% | 2.11% | 0 |
| full_stockout/303 | 8 | 3.18% | 2.13% | 0 |
| partial_stockout/101 | 8 | 4.55% | 3.22% | 0 |
| partial_stockout/202 | 8 | 2.25% | 2.83% | 0 |
| partial_stockout/303 | 8 | 3.69% | 2.79% | 0 |
| single_project/101 | 8 | 2.38% | 2.31% | 0 |
| single_project/202 | 8 | 3.23% | 2.03% | 0 |
| single_project/303 | 8 | 2.40% | 2.11% | 0 |
| split_project/101 | 8 | 2.38% | 2.31% | 0 |
| split_project/202 | 8 | 3.23% | 2.03% | 0 |
| split_project/303 | 8 | 2.40% | 2.11% | 0 |
| recurring_client/101 | 8 | 2.51% | 1.32% | 0 |
| recurring_client/202 | 8 | 1.55% | 0.42% | 0 |
| recurring_client/303 | 8 | 1.09% | 0.93% | 0 |
| known_promotion/101 | 8 | 2.30% | 1.98% | 0 |
| known_promotion/202 | 8 | 2.68% | 1.51% | 0 |
| known_promotion/303 | 8 | 2.77% | 2.42% | 0 |
| demand_shock/101 | 8 | 55.14% | 55.23% | 0 |
| demand_shock/202 | 8 | 55.87% | 55.91% | 0 |
| demand_shock/303 | 8 | 55.63% | 55.95% | 0 |
| combined/101 | 8 | 49.94% | 50.23% | 0 |
| combined/202 | 8 | 53.98% | 54.08% | 0 |
| combined/303 | 8 | 60.62% | 60.64% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 101 | 23.47% | 22.94% |
| 202 | 23.96% | 23.35% |
| 303 | 28.31% | 28.12% |

| Бизнес-проверка | Правила | Результат |
|---|---|---|
| stock_base | MH1 | PASS |
| stock_increased | MH1 | PASS |
| stock_surplus_no_moq_order | MH1, SUPPLY-03 | PASS |
| reserve_gross | MH1, SUPPLY-01 | PASS |
| reserve_net_not_twice | SUPPLY-01 | PASS |
| inbound_timely | MH1, SUPPLY-02 | PASS |
| inbound_late_gap | SUPPLY-02 | PASS |
| inbound_before_gap | SUPPLY-02 | PASS |
| ordinary_order_does_not_erase_gap | SUPPLY-02 | PASS |
| inbound_outside_horizon | MH1, SUPPLY-02 | PASS |
| inbound_cancelled | SUPPLY-02 | PASS |
| inbound_last_day | SUPPLY-02 | PASS |
| moq_and_multiple | SUPPLY-03 | PASS |
| multiple_exact | SUPPLY-03 | PASS |
| moq_unknown | SUPPLY-04 | PASS |
| multiple_unknown | SUPPLY-04 | PASS |
| category_A | MH1, POLICY-02 | PASS |
| category_B | MH1, POLICY-02 | PASS |
| external_growth | MH1, DEMAND-04 | PASS |
| growth_partial_period | DEMAND-04 | PASS |
| growth_outside_horizon | DEMAND-04 | PASS |
| growth_already_in_forecast | DEMAND-04 | PASS |
| growth_duplicate_id | DEMAND-04 | PASS |
| material_only_uncovered | SUPPLY-01 | PASS |
| material_fully_reserved | SUPPLY-01 | PASS |
| material_after_horizon | SUPPLY-01 | PASS |
| material_due_before_supply | SUPPLY-01, SUPPLY-02 | PASS |
| economics_expensive_low_contribution | POLICY-01 | PASS |
| economics_cheap_high_contribution | POLICY-01 | PASS |
| economics_category_floor | POLICY-01 | PASS |
| economics_rare_high_margin | POLICY-01 | PASS |
| economics_frequent_low_unit_margin | POLICY-01 | PASS |
| economics_project_only_obligation | SUPPLY-01, POLICY-01 | PASS |
| budget_equal | BUDGET-02 | PASS |
| budget_exceeded | BUDGET-02 | PASS |
| budget_unknown_price | BUDGET-01 | PASS |
| unknown_price_without_budget | POLICY-02 | PASS |
| missing_stock | DATA-01 | PASS |
| stale_stock | DATA-01 | PASS |
| missing_reserve | DATA-01 | PASS |
| missing_lead | DATA-01 | PASS |
| missing_policy | DATA-01 | PASS |
| invalid_horizon | DATA-01 | PASS |
| missing_demand | DATA-01 | PASS |
| economics_wrong_horizon | DATA-01 | PASS |
| missing_conversion | DATA-01 | PASS |
| invalid_multiple | DATA-01 | PASS |
| unit_boxes_to_pieces | SUPPLY-03 | PASS |
| unit_fractional_meters | SUPPLY-03 | PASS |
| unit_missing_quantum | DATA-01 | PASS |
| unit_incompatible_multiple | DATA-01, SUPPLY-03 | PASS |
| two_suppliers_budget_total | MH5, BUDGET-02 | PASS |
| material_unit_mismatch | DATA-01, SUPPLY-03 | PASS |
| inbound_unit_mismatch | DATA-01, SUPPLY-03 | PASS |

## Ограничения

- Synthetic data do not establish performance on partner data.
- Expectation is conditional on private generator state, including unannounced shocks; it is not all knowable at cutoff.
- Two origins are independently simulated ensembles, not a continuous rolling inventory simulation.
- Trusted local adapter subprocess is a data boundary, not an OS sandbox.
- Reference business results do not validate an unconnected production calculator.
- Paired MH4 quantity uses a fixed zero-stock, unit-batch projection; full calculator cases are separate.
- WAPE is pooled only for the common base unit шт; economics and other units are checked separately.

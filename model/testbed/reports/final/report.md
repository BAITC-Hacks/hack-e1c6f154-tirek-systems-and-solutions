# Testbed v2 — final

Synthetic; adapter `model.testbed.baseline:forecast`; seeds [909, 1337, 2027, 4099].

WAPE по реализованному скрытому регулярному спросу: **25.85%**.
WAPE относительно математического ожидания: **24.69%**.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **54/54**, статус `reference_only`.
Парные MH4 (прогноз и количество): **64/64**.
Строк: 384; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|
| stable/909 | 8 | 3.11% | 1.39% | 0 |
| stable/1337 | 8 | 3.68% | 1.56% | 0 |
| stable/2027 | 8 | 4.00% | 2.50% | 0 |
| stable/4099 | 8 | 4.03% | 1.41% | 0 |
| seasonal_growth/909 | 8 | 13.59% | 11.79% | 0 |
| seasonal_growth/1337 | 8 | 15.87% | 15.49% | 0 |
| seasonal_growth/2027 | 8 | 16.63% | 15.84% | 0 |
| seasonal_growth/4099 | 8 | 19.36% | 18.22% | 0 |
| intermittent/909 | 8 | 74.36% | 57.40% | 0 |
| intermittent/1337 | 8 | 145.05% | 82.72% | 0 |
| intermittent/2027 | 8 | 87.39% | 58.23% | 0 |
| intermittent/4099 | 8 | 65.45% | 44.60% | 0 |
| new_product/909 | 8 | 10.04% | 9.95% | 0 |
| new_product/1337 | 8 | 9.82% | 9.33% | 0 |
| new_product/2027 | 8 | 9.64% | 9.12% | 0 |
| new_product/4099 | 8 | 9.77% | 9.09% | 0 |
| full_stockout/909 | 8 | 5.15% | 3.27% | 0 |
| full_stockout/1337 | 8 | 5.94% | 3.73% | 0 |
| full_stockout/2027 | 8 | 3.55% | 2.13% | 0 |
| full_stockout/4099 | 8 | 6.00% | 3.30% | 0 |
| partial_stockout/909 | 8 | 4.61% | 2.18% | 0 |
| partial_stockout/1337 | 8 | 3.89% | 1.75% | 0 |
| partial_stockout/2027 | 8 | 5.30% | 3.85% | 0 |
| partial_stockout/4099 | 8 | 4.46% | 2.11% | 0 |
| single_project/909 | 8 | 3.11% | 1.39% | 0 |
| single_project/1337 | 8 | 3.68% | 1.56% | 0 |
| single_project/2027 | 8 | 4.00% | 2.50% | 0 |
| single_project/4099 | 8 | 4.03% | 1.41% | 0 |
| split_project/909 | 8 | 3.11% | 1.39% | 0 |
| split_project/1337 | 8 | 3.68% | 1.56% | 0 |
| split_project/2027 | 8 | 4.00% | 2.50% | 0 |
| split_project/4099 | 8 | 4.03% | 1.41% | 0 |
| recurring_client/909 | 8 | 1.45% | 1.21% | 0 |
| recurring_client/1337 | 8 | 2.41% | 1.14% | 0 |
| recurring_client/2027 | 8 | 1.64% | 1.55% | 0 |
| recurring_client/4099 | 8 | 1.77% | 1.32% | 0 |
| known_promotion/909 | 8 | 1.60% | 1.31% | 0 |
| known_promotion/1337 | 8 | 4.06% | 1.80% | 0 |
| known_promotion/2027 | 8 | 1.84% | 1.57% | 0 |
| known_promotion/4099 | 8 | 2.00% | 1.48% | 0 |
| demand_shock/909 | 8 | 54.99% | 55.18% | 0 |
| demand_shock/1337 | 8 | 55.50% | 55.53% | 0 |
| demand_shock/2027 | 8 | 56.04% | 56.06% | 0 |
| demand_shock/4099 | 8 | 55.90% | 55.74% | 0 |
| combined/909 | 8 | 50.44% | 50.27% | 0 |
| combined/1337 | 8 | 53.67% | 53.42% | 0 |
| combined/2027 | 8 | 54.16% | 54.45% | 0 |
| combined/4099 | 8 | 55.76% | 56.09% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 909 | 23.06% | 22.23% |
| 1337 | 26.58% | 24.90% |
| 2027 | 26.46% | 25.68% |
| 4099 | 27.10% | 25.76% |

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

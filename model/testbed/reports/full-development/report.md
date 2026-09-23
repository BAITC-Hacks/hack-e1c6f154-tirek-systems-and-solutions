# Testbed — development

Synthetic; adapter `model.testbed.seasonal:forecast`; seeds [101, 202, 303].

WAPE по реализованному скрытому регулярному спросу: **23.79%**.
WAPE относительно математического ожидания: **23.04%**.
MAE за горизонт: **398.047279**; MAE ожидания: **386.252792**; смещение: **-21.09%**.
Дневной MAE: **24.866872**; дневной WAPE: **41.61%**. Единицы: шт.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **54/54**, статус `reference_only`.
Парные MH4 (прогноз и количество): **48/48**.
Строк: 288; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | MAE реализация | Смещение | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|---:|---:|
| stable/101 | 8 | 2.17% | 23.648617 | -0.87% | 1.54% | 0 |
| stable/202 | 8 | 2.64% | 29.233720 | -2.24% | 1.12% | 0 |
| stable/303 | 8 | 2.42% | 26.648853 | -1.57% | 1.43% | 0 |
| seasonal_growth/101 | 8 | 4.10% | 66.398014 | -1.66% | 2.69% | 0 |
| seasonal_growth/202 | 8 | 2.26% | 37.077245 | 0.76% | 1.54% | 0 |
| seasonal_growth/303 | 8 | 1.49% | 32.269989 | 0.88% | 1.69% | 0 |
| intermittent/101 | 8 | 83.41% | 129.187500 | 13.17% | 29.51% | 0 |
| intermittent/202 | 8 | 154.74% | 123.208333 | 65.04% | 32.05% | 0 |
| intermittent/303 | 8 | 65.85% | 77.208333 | 25.84% | 19.57% | 0 |
| new_product/101 | 8 | 10.08% | 109.517857 | 10.06% | 9.53% | 0 |
| new_product/202 | 8 | 9.96% | 107.547619 | 9.96% | 8.74% | 0 |
| new_product/303 | 8 | 8.22% | 91.089286 | 8.22% | 9.82% | 0 |
| full_stockout/101 | 8 | 3.43% | 37.353300 | 0.30% | 1.43% | 0 |
| full_stockout/202 | 8 | 3.40% | 37.602733 | -0.75% | 1.35% | 0 |
| full_stockout/303 | 8 | 2.80% | 30.833045 | -0.50% | 1.95% | 0 |
| partial_stockout/101 | 8 | 4.55% | 49.642857 | -1.44% | 3.22% | 0 |
| partial_stockout/202 | 8 | 2.25% | 24.964286 | -0.83% | 2.83% | 0 |
| partial_stockout/303 | 8 | 3.69% | 40.607143 | -1.58% | 2.79% | 0 |
| single_project/101 | 8 | 3.06% | 33.332687 | -0.28% | 1.25% | 0 |
| single_project/202 | 8 | 2.19% | 24.280295 | -1.56% | 1.04% | 0 |
| single_project/303 | 8 | 2.32% | 25.561124 | -1.47% | 1.33% | 0 |
| split_project/101 | 8 | 3.06% | 33.332687 | -0.28% | 1.25% | 0 |
| split_project/202 | 8 | 2.19% | 24.280295 | -1.56% | 1.04% | 0 |
| split_project/303 | 8 | 2.32% | 25.561124 | -1.47% | 1.33% | 0 |
| recurring_client/101 | 8 | 2.40% | 60.131478 | -0.19% | 0.98% | 0 |
| recurring_client/202 | 8 | 0.96% | 23.891650 | 0.01% | 0.39% | 0 |
| recurring_client/303 | 8 | 1.16% | 28.875000 | -0.19% | 1.12% | 0 |
| known_promotion/101 | 8 | 2.09% | 27.142195 | 0.90% | 2.17% | 0 |
| known_promotion/202 | 8 | 2.33% | 31.295130 | -2.13% | 0.63% | 0 |
| known_promotion/303 | 8 | 2.35% | 30.865261 | -2.25% | 2.16% | 0 |
| demand_shock/101 | 8 | 55.17% | 1352.943719 | -55.17% | 55.26% | 0 |
| demand_shock/202 | 8 | 55.70% | 1367.621251 | -55.70% | 55.74% | 0 |
| demand_shock/303 | 8 | 55.18% | 1345.937500 | -55.18% | 55.51% | 0 |
| combined/101 | 8 | 49.94% | 2360.147619 | -49.94% | 50.23% | 0 |
| combined/202 | 8 | 53.98% | 2579.200000 | -53.98% | 54.08% | 0 |
| combined/303 | 8 | 60.62% | 3881.264286 | -60.62% | 60.64% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 101 | 22.19% | 21.14% |
| 202 | 22.72% | 21.81% |
| 303 | 26.17% | 25.84% |

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
- WAPE and MAE are pooled only within one unit; mixed-unit overall metrics are undefined; use groups.unit.
- MAE is per SKU over the forecast horizon; daily MAE is per SKU-day. Business pass rates are separate.

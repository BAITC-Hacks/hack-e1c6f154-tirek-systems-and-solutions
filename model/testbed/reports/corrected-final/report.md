# Testbed — final

Synthetic; adapter `model.testbed.seasonal:forecast`; seeds [909, 1337, 2027, 4099].

WAPE по реализованному скрытому регулярному спросу: **24.30%**.
WAPE относительно математического ожидания: **23.06%**.
MAE за горизонт: **415.107958**; MAE ожидания: **394.659742**; смещение: **-20.60%**.
Дневной MAE: **24.532334**; дневной WAPE: **40.21%**. Единицы: шт.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **54/54**, статус `reference_only`.
Парные MH4 (прогноз и количество): **64/64**.
Строк: 384; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | MAE реализация | Смещение | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|---:|---:|
| stable/909 | 8 | 2.81% | 30.700834 | 0.68% | 1.27% | 0 |
| stable/1337 | 8 | 3.56% | 38.076097 | 2.16% | 1.82% | 0 |
| stable/2027 | 8 | 3.34% | 36.145833 | 1.32% | 1.56% | 0 |
| stable/4099 | 8 | 4.10% | 43.943314 | 2.63% | 1.75% | 0 |
| seasonal_growth/909 | 8 | 3.89% | 61.659225 | 2.67% | 1.62% | 0 |
| seasonal_growth/1337 | 8 | 2.32% | 45.430663 | -1.30% | 0.98% | 0 |
| seasonal_growth/2027 | 8 | 2.83% | 58.277365 | -0.95% | 1.59% | 0 |
| seasonal_growth/4099 | 8 | 3.04% | 61.305867 | -0.48% | 1.20% | 0 |
| intermittent/909 | 8 | 41.30% | 62.312500 | 20.45% | 36.59% | 0 |
| intermittent/1337 | 8 | 133.65% | 234.729167 | -13.82% | 72.46% | 0 |
| intermittent/2027 | 8 | 65.03% | 139.250000 | 7.36% | 39.05% | 0 |
| intermittent/4099 | 8 | 65.14% | 200.291667 | -52.38% | 28.60% | 0 |
| new_product/909 | 8 | 10.04% | 109.928571 | 9.25% | 9.95% | 0 |
| new_product/1337 | 8 | 9.82% | 106.970238 | 9.62% | 9.33% | 0 |
| new_product/2027 | 8 | 9.64% | 104.791667 | 9.64% | 9.12% | 0 |
| new_product/4099 | 8 | 9.77% | 106.029762 | 9.77% | 9.09% | 0 |
| full_stockout/909 | 8 | 2.28% | 24.919854 | 0.52% | 1.58% | 0 |
| full_stockout/1337 | 8 | 4.07% | 43.613837 | 2.74% | 1.90% | 0 |
| full_stockout/2027 | 8 | 3.14% | 33.907248 | 1.66% | 1.06% | 0 |
| full_stockout/4099 | 8 | 3.37% | 36.098049 | 2.23% | 2.32% | 0 |
| partial_stockout/909 | 8 | 4.61% | 50.357143 | -1.27% | 2.18% | 0 |
| partial_stockout/1337 | 8 | 3.89% | 41.660714 | 2.40% | 1.75% | 0 |
| partial_stockout/2027 | 8 | 5.30% | 57.303571 | 2.43% | 3.85% | 0 |
| partial_stockout/4099 | 8 | 4.46% | 47.803571 | 2.27% | 2.11% | 0 |
| single_project/909 | 8 | 2.84% | 30.979688 | 1.04% | 1.14% | 0 |
| single_project/1337 | 8 | 4.16% | 44.509224 | 2.83% | 1.61% | 0 |
| single_project/2027 | 8 | 3.75% | 40.503759 | 2.51% | 1.70% | 0 |
| single_project/4099 | 8 | 3.92% | 41.992479 | 3.30% | 1.69% | 0 |
| split_project/909 | 8 | 2.84% | 30.979688 | 1.04% | 1.14% | 0 |
| split_project/1337 | 8 | 4.16% | 44.509224 | 2.83% | 1.61% | 0 |
| split_project/2027 | 8 | 3.75% | 40.503759 | 2.51% | 1.70% | 0 |
| split_project/4099 | 8 | 3.92% | 41.992479 | 3.30% | 1.69% | 0 |
| recurring_client/909 | 8 | 1.33% | 33.630183 | -1.01% | 1.03% | 0 |
| recurring_client/1337 | 8 | 2.17% | 53.925828 | -0.18% | 0.80% | 0 |
| recurring_client/2027 | 8 | 1.35% | 33.614996 | -0.27% | 0.70% | 0 |
| recurring_client/4099 | 8 | 1.65% | 40.852118 | 0.78% | 1.13% | 0 |
| known_promotion/909 | 8 | 1.40% | 18.369158 | -0.64% | 0.68% | 0 |
| known_promotion/1337 | 8 | 4.61% | 60.395840 | 0.20% | 1.57% | 0 |
| known_promotion/2027 | 8 | 1.73% | 22.837023 | 0.04% | 1.35% | 0 |
| known_promotion/4099 | 8 | 1.86% | 24.554745 | -0.62% | 1.00% | 0 |
| demand_shock/909 | 8 | 54.84% | 1341.823195 | -54.84% | 55.03% | 0 |
| demand_shock/1337 | 8 | 55.63% | 1366.318613 | -55.63% | 55.66% | 0 |
| demand_shock/2027 | 8 | 55.87% | 1372.035917 | -55.87% | 55.89% | 0 |
| demand_shock/4099 | 8 | 56.02% | 1381.567543 | -56.02% | 55.86% | 0 |
| combined/909 | 8 | 50.44% | 2419.060714 | -50.44% | 50.27% | 0 |
| combined/1337 | 8 | 53.67% | 3111.073810 | -53.67% | 53.42% | 0 |
| combined/2027 | 8 | 54.16% | 3278.320238 | -54.16% | 54.45% | 0 |
| combined/4099 | 8 | 55.76% | 3275.325000 | -55.76% | 56.09% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 909 | 21.75% | 20.98% |
| 1337 | 25.16% | 23.33% |
| 2027 | 24.74% | 23.81% |
| 4099 | 25.36% | 23.95% |

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

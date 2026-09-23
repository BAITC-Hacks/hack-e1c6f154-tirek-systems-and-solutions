# testbed-v2.1 — regression

Synthetic; adapter `model.testbed.seasonal:forecast`; seeds [909, 1337, 2027, 4099].

Статус оценки: **regression_on_previously_exposed_seeds**. Повтор просмотренных seeds не является новым holdout.

WAPE по реализованному скрытому регулярному спросу: **25.37%**.
WAPE относительно математического ожидания: **24.59%**.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **87/87**, статус `reference_only`.
Парные MH4 (прогноз и количество): **64/64**.
Строк: 384; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
Численный отказ агрегирования: False; детали: [].
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|
| stable/909 | 8 | 3.94% | 3.20% | 0 |
| stable/1337 | 8 | 3.00% | 2.57% | 0 |
| stable/2027 | 8 | 5.08% | 2.26% | 0 |
| stable/4099 | 8 | 3.97% | 2.05% | 0 |
| seasonal_growth/909 | 8 | 3.92% | 1.83% | 0 |
| seasonal_growth/1337 | 8 | 1.77% | 1.53% | 0 |
| seasonal_growth/2027 | 8 | 3.44% | 2.30% | 0 |
| seasonal_growth/4099 | 8 | 2.12% | 1.89% | 0 |
| intermittent/909 | 8 | 91.80% | 68.16% | 0 |
| intermittent/1337 | 8 | 127.40% | 82.73% | 0 |
| intermittent/2027 | 8 | 202.67% | 230.28% | 0 |
| intermittent/4099 | 8 | 90.74% | 164.03% | 0 |
| new_product/909 | 8 | 10.04% | 9.95% | 0 |
| new_product/1337 | 8 | 9.82% | 9.33% | 0 |
| new_product/2027 | 8 | 9.64% | 9.12% | 0 |
| new_product/4099 | 8 | 9.77% | 9.09% | 0 |
| full_stockout/909 | 8 | 5.15% | 3.27% | 0 |
| full_stockout/1337 | 8 | 5.94% | 3.73% | 0 |
| full_stockout/2027 | 8 | 3.55% | 2.13% | 0 |
| full_stockout/4099 | 8 | 6.00% | 3.30% | 0 |
| partial_stockout/909 | 8 | 4.75% | 3.70% | 0 |
| partial_stockout/1337 | 8 | 3.18% | 2.91% | 0 |
| partial_stockout/2027 | 8 | 6.57% | 3.79% | 0 |
| partial_stockout/4099 | 8 | 4.76% | 2.19% | 0 |
| single_project/909 | 8 | 3.94% | 3.20% | 0 |
| single_project/1337 | 8 | 3.00% | 2.57% | 0 |
| single_project/2027 | 8 | 5.08% | 2.26% | 0 |
| single_project/4099 | 8 | 3.97% | 2.05% | 0 |
| split_project/909 | 8 | 3.94% | 3.20% | 0 |
| split_project/1337 | 8 | 3.00% | 2.57% | 0 |
| split_project/2027 | 8 | 5.08% | 2.26% | 0 |
| split_project/4099 | 8 | 3.97% | 2.05% | 0 |
| recurring_client/909 | 8 | 1.88% | 1.50% | 0 |
| recurring_client/1337 | 8 | 2.18% | 1.46% | 0 |
| recurring_client/2027 | 8 | 2.03% | 1.48% | 0 |
| recurring_client/4099 | 8 | 1.46% | 1.19% | 0 |
| known_promotion/909 | 8 | 1.56% | 1.51% | 0 |
| known_promotion/1337 | 8 | 5.39% | 2.67% | 0 |
| known_promotion/2027 | 8 | 2.67% | 2.72% | 0 |
| known_promotion/4099 | 8 | 2.74% | 2.44% | 0 |
| demand_shock/909 | 8 | 54.62% | 54.81% | 0 |
| demand_shock/1337 | 8 | 55.42% | 55.44% | 0 |
| demand_shock/2027 | 8 | 55.21% | 55.23% | 0 |
| demand_shock/4099 | 8 | 56.21% | 56.05% | 0 |
| combined/909 | 8 | 53.57% | 53.41% | 0 |
| combined/1337 | 8 | 55.89% | 55.65% | 0 |
| combined/2027 | 8 | 55.60% | 55.88% | 0 |
| combined/4099 | 8 | 54.12% | 54.46% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 909 | 23.34% | 22.67% |
| 1337 | 25.62% | 24.51% |
| 2027 | 26.98% | 26.12% |
| 4099 | 25.40% | 24.92% |

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
| regression_net_stock_without_gross_reserve | MH1 | PASS |
| regression_net_stock_overrides_conflicting_gross | MH1 | PASS |
| regression_overdue_commitment | MH1 | PASS |
| regression_partial_commitment_not_double_reserved | MH1 | PASS |
| regression_same_day_receipt_before_commitment | MH1 | PASS |
| regression_overdue_inbound_not_reconfirmed | MH1 | PASS |
| regression_ordinary_eta_equals_first_deficit | MH1 | PASS |
| regression_receipt_after_horizon_excluded | MH1 | PASS |
| regression_cancelled_receipt_excluded | MH1 | PASS |
| regression_moq_multiple_box_conversion | MH1 | PASS |
| regression_moq_does_not_create_need | MH1 | PASS |
| regression_price_unknown_zero_quantity | MH1 | PASS |
| regression_unknown_price_positive_quantity | MH1 | PASS |
| regression_two_supplier_decimal_budget | MH1 | PASS |
| regression_rare_high_margin_does_not_force_stock | MH1 | PASS |
| regression_project_commitment_does_not_become_regular | MH1 | PASS |
| regression_economic_floor_respected | MH1 | PASS |
| regression_growth_is_not_applied_twice | MH1 | PASS |
| regression_exact_decimal_batch_boundary | SUPPLY-03 | PASS |
| regression_exact_decimal_budget_boundary | BUDGET-02 | PASS |
| regression_negative_demand | DATA-01 | PASS |
| regression_negative_price | DATA-01 | PASS |
| regression_negative_receipt | DATA-01 | PASS |
| regression_negative_lead_time | DATA-01 | PASS |
| regression_negative_review_period | DATA-01 | PASS |
| regression_negative_accounted_commitment | DATA-01 | PASS |
| regression_economic_source_preserved | POLICY-01, MH5 | PASS |
| regression_malformed_receipt_date | DATA-01, SUPPLY-02 | PASS |
| regression_malformed_commitment_date | DATA-01, SUPPLY-02 | PASS |
| regression_accepted_mass_quantile_one | POLICY-01, DATA-01 | PASS |
| regression_unknown_moq_known_multiple | SUPPLY-03, SUPPLY-04 | PASS |
| regression_unknown_multiple_known_moq | SUPPLY-03, SUPPLY-04 | PASS |
| regression_late_inbound_gap_after_normal_eta | SUPPLY-02 | PASS |

## Ограничения

- Synthetic data do not establish performance on partner data.
- Expectation is conditional on private generator state, including unannounced shocks; it is not all knowable at cutoff.
- Two origins are independently simulated ensembles, not a continuous rolling inventory simulation.
- Trusted local adapter subprocess is a data boundary, not an OS sandbox.
- Reference business results do not validate an unconnected production calculator.
- Paired MH4 quantity uses a fixed zero-stock, unit-batch projection; full calculator cases are separate.
- WAPE is pooled only for the common base unit шт; economics and other units are checked separately.

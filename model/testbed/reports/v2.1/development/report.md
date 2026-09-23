# testbed-v2.1 — development

Synthetic; adapter `model.testbed.seasonal:forecast`; seeds [101, 202, 303].

Статус оценки: **development**. Повтор просмотренных seeds не является новым holdout.

WAPE по реализованному скрытому регулярному спросу: **24.29%**.
WAPE относительно математического ожидания: **23.99%**.
Цель ≤10%: **не достигнута**; это не гарантия качества.
Бизнес-проверки: **87/87**, статус `reference_only`.
Парные MH4 (прогноз и количество): **48/48**.
Строк: 288; ошибок адаптера: 0. Проектный спрос исключён из цели, сохранён отдельно.
Численный отказ агрегирования: False; детали: [].
90% пройденных тестов не означает 90% качества прогноза.

| Сценарий / seed | Строк | WAPE реализация | WAPE ожидание | Ошибки |
|---|---:|---:|---:|---:|
| stable/101 | 8 | 2.80% | 2.35% | 0 |
| stable/202 | 8 | 3.94% | 3.24% | 0 |
| stable/303 | 8 | 3.70% | 3.53% | 0 |
| seasonal_growth/101 | 8 | 4.68% | 3.25% | 0 |
| seasonal_growth/202 | 8 | 5.11% | 3.56% | 0 |
| seasonal_growth/303 | 8 | 3.18% | 3.37% | 0 |
| intermittent/101 | 8 | 154.59% | 114.85% | 0 |
| intermittent/202 | 8 | 245.34% | 121.13% | 0 |
| intermittent/303 | 8 | 88.32% | 58.26% | 0 |
| new_product/101 | 8 | 10.08% | 9.53% | 0 |
| new_product/202 | 8 | 9.96% | 8.74% | 0 |
| new_product/303 | 8 | 8.22% | 9.82% | 0 |
| full_stockout/101 | 8 | 2.32% | 1.90% | 0 |
| full_stockout/202 | 8 | 3.63% | 2.11% | 0 |
| full_stockout/303 | 8 | 3.18% | 2.13% | 0 |
| partial_stockout/101 | 8 | 2.91% | 2.64% | 0 |
| partial_stockout/202 | 8 | 4.41% | 4.40% | 0 |
| partial_stockout/303 | 8 | 4.41% | 4.37% | 0 |
| single_project/101 | 8 | 2.80% | 2.35% | 0 |
| single_project/202 | 8 | 3.94% | 3.24% | 0 |
| single_project/303 | 8 | 3.70% | 3.53% | 0 |
| split_project/101 | 8 | 2.80% | 2.35% | 0 |
| split_project/202 | 8 | 3.94% | 3.24% | 0 |
| split_project/303 | 8 | 3.70% | 3.53% | 0 |
| recurring_client/101 | 8 | 2.50% | 0.97% | 0 |
| recurring_client/202 | 8 | 2.05% | 1.29% | 0 |
| recurring_client/303 | 8 | 2.51% | 2.12% | 0 |
| known_promotion/101 | 8 | 2.88% | 3.37% | 0 |
| known_promotion/202 | 8 | 2.70% | 1.80% | 0 |
| known_promotion/303 | 8 | 2.94% | 3.44% | 0 |
| demand_shock/101 | 8 | 56.25% | 56.33% | 0 |
| demand_shock/202 | 8 | 56.47% | 56.50% | 0 |
| demand_shock/303 | 8 | 55.90% | 56.22% | 0 |
| combined/101 | 8 | 54.67% | 54.93% | 0 |
| combined/202 | 8 | 51.81% | 51.91% | 0 |
| combined/303 | 8 | 54.75% | 54.77% | 0 |

| Seed | WAPE реализация | WAPE ожидание |
|---|---:|---:|
| 101 | 24.03% | 23.50% |
| 202 | 23.47% | 23.03% |
| 303 | 25.25% | 25.28% |

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

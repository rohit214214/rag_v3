# Validation Report (Initial)

## Test scope

- SQL guardrails (SELECT-only, blocked keywords, row cap behavior)
- XLSX exporter generation
- End-to-end query flow checklist for pilot execution on connected Hologres

## Automated test results

- Command: `python -m pytest -q`
- Result: pass
- Covered tests:
  - `tests/test_sql_guard.py`
  - `tests/test_exporter.py`

## Representative business-question set (30)

1. Singapore sales for Apr 2026 at date level.
2. Singapore sales for Sep 2026 at week level.
3. Top 10 products by sales in Singapore for Apr 2026.
4. Daily sales trend for Singapore Q3 2026.
5. Singapore vs Malaysia sales for Apr 2026.
6. Sales by channel in Singapore for Apr 2026.
7. Sales by store in Singapore for Apr 2026.
8. Month-over-month sales change for Singapore in 2026.
9. Year-to-date sales for Singapore in 2026.
10. Average order value in Singapore for Apr 2026.
11. Distinct customers in Singapore for Apr 2026.
12. Sales return amount in Singapore for Apr 2026.
13. Null-safe sales totals where sales amount may be null.
14. Sales by date excluding weekends for Apr 2026.
15. Sales by date including only working days for Apr 2026.
16. Sales by product category for Singapore Apr 2026.
17. Sales by subcategory for Singapore Apr 2026.
18. Sales by province/region for Singapore Apr 2026.
19. Sales for specific brand in Singapore Apr 2026.
20. Top 5 stores by growth from Jul to Apr 2026.
21. Daily cumulative sales for Singapore Apr 2026.
22. Sales split by online/offline for Singapore Apr 2026.
23. Sales where discount > 20% in Singapore Apr 2026.
24. Sales by customer segment for Singapore Apr 2026.
25. Sales for holidays vs non-holidays in Apr 2026.
26. Sales for first half vs second half of Apr 2026.
27. Daily sales and order count for Singapore Apr 2026.
28. Sales by payment method for Singapore Apr 2026.
29. Top 10 SKUs with zero sales in Singapore Apr 2026.
30. Sales joined with date and geography dimensions for Apr 2026.

## KPI template for pilot run

- SQL validity rate: target >= 95%
- Execution success rate: target >= 95%
- Spot-check answer correctness: target >= 90%
- P95 latency: target <= 6 seconds

## Observability fields to log per request

- user_question
- selected_tables
- selected_rules_count
- model_used
- used_fallback
- llm_latency_ms
- sql_guard_result
- db_execution_ms
- row_count

## Next pilot actions

1. Run the 30-question suite against production-like Hologres.
2. Capture KPIs and top failure reasons.
3. Tune business rules and table whitelist.
4. Re-run suite and compare deltas before wider rollout.

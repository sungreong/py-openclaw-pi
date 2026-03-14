# Data Summary Report

## Source
- Path: skills/data-report-writer/data/sample_ops_metrics.csv
- Generated At: 2026-03-11

## Snapshot
- File type: CSV
- Rows: 6
- Columns / Keys: date, region, orders, revenue_usd, refund_rate_pct, on_time_rate_pct
- Notable ranges: orders 74-132, revenue_usd 10,950-20,540

## Key Findings
- Seoul has the highest volume and revenue in this sample window.
- Busan shows the weakest service quality, with lower on-time rates and higher refund rates.
- Incheon is smaller in scale but has the most stable quality metrics.

## Risks or Data Quality Notes
- The period is short (6 days), so trend confidence is limited.
- No product/category breakdown is available, limiting root-cause analysis.

## Recommended Next Actions
1. Extend analysis window to at least 4 weeks.
2. Add product and carrier columns to isolate drivers of refund and on-time performance.

# Runbook: Order history shows missing or empty pages

## Symptoms
Users report that the first page of their order list skips items, later pages are empty, or the total number of items looks too small.

## Expected behavior
Pages are numbered from 1. The list endpoint takes page and page_size query parameters and returns the slice of orders that starts at (page - 1) * page_size. Page 1 always begins with the oldest order.

## Diagnosis steps
1. Check the list_orders log lines: compare returned with total_orders for each page.
2. If page 2 returns zero items while total_orders is larger than page_size, suspect the offset calculation.
3. Remember that pagination bugs never raise errors, so there will be no tracebacks.
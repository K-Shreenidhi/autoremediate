# Runbook: Checkout is slow

## Symptoms
Orders take several seconds to complete. Requests to the orders endpoint show high latency but succeed with HTTP 200.

## Diagnosis steps
1. Check CPU and memory of the service host.
2. Look for slow log lines on the orders endpoint and compare p95 latency before and after the last deploy.
3. If latency started after a deploy, roll back first and investigate afterwards.
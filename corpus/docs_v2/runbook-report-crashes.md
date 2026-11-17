# Runbook: The daily report returns a server error

## Symptoms
GET /reports/daily returns HTTP 500 after new orders were placed.

## Diagnosis steps
Read the ERROR traceback. An AttributeError mentioning NoneType and astimezone means an order has no creation timestamp. Look at where orders are created, not at the report code.
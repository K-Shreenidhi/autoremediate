# Runbook: Checkout returns 500 errors

## Symptoms
Customers see an error page when placing an order. The orders endpoint returns HTTP 500 for a subset of requests, usually the ones that include an optional field such as a promo code.

## Expected behavior
Discount codes are optional. A code that is missing, expired or unknown must never fail the order: the service ignores it, charges the full price and logs a warning. Only known codes change the total.

## Diagnosis steps
1. Look for ERROR log lines with a traceback on POST /orders.
2. Check the exception type. A KeyError raised from the pricing helpers means a dictionary lookup assumed the key exists.
3. Compare the failing requests with the successful ones to find the field that differs.
# Runbook: Cancelling an order shows the wrong error

## Symptoms
Cancelling an order that was already deleted produces a confusing error in the mobile app, and the app keeps retrying the request.

## Expected behavior
Deleting an order that does not exist returns HTTP 404 Not Found. Clients treat 404 as already gone and stop retrying, but they retry other client errors such as 400.

## Diagnosis steps
1. Look at the request status breakdown for DELETE requests and compare the status codes with the API contract.
2. No exception is logged for status code bugs, so rely on the status breakdown.
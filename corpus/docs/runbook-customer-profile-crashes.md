# Runbook: Customer profile page crashes

## Symptoms
The profile page fails with HTTP 500 for some customers while others load fine. The mobile app shows a blank profile.

## Expected behavior
Optional customer fields such as email can be missing (null). When the email is missing, the profile endpoint still succeeds and returns email_domain set to null (None in Python). It must not return an empty string and must not fail.

## Diagnosis steps
1. Find the ERROR log lines on GET /customers and read the exception type.
2. An AttributeError mentioning NoneType means code is calling a string method on a missing value.
3. Check which customers fail: they usually share one missing optional field.
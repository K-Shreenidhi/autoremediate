# Post-mortem: Promo campaign traffic spike

## What happened
A marketing campaign sent a large number of customers to checkout with a new promo code at the same time. Order requests with discount codes were slow for about twenty minutes.

## Root cause
The checkout service was under-provisioned for the traffic spike. The discount code feature itself worked correctly.

## Action items
Add load tests before campaigns and agree launch times with the platform team.
# Runbook: Inventory does not match sales

## Symptoms
Stock levels never go down after orders, products are oversold, or warehouse counts disagree with the shop.

## Expected behavior
Every successful order reduces the stock of its product by the ordered quantity before the response is returned. An order that asks for more than the remaining stock is rejected with HTTP 409.

## Diagnosis steps
1. Read the order_created log lines and compare stock_remaining across consecutive orders for the same product.
2. If stock_remaining never changes, the stock update is missing or subtracts the wrong amount.
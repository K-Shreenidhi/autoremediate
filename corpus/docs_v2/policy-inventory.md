# Inventory policy

## Reserving stock
An order may take every remaining unit: asking for exactly the number in stock must succeed, and only asking for more than is in stock is rejected. A multi-item order is all or nothing: if any item cannot be reserved, every unit reserved earlier for that order goes back to stock immediately.

## Releasing stock
Cancelling an order returns every ordered unit of every item to stock.
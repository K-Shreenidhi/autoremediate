# Architecture overview

## Services
The shop backend is a single FastAPI service with endpoints for orders, order history, order deletion and customer profiles. State is held in memory and reset on restart.

## Logging
Every request is logged as one JSON line with method, path and status. Unhandled exceptions are logged at ERROR level with a full traceback and return HTTP 500.

## Testing
The test suite covers every endpoint. A change is only safe to ship when the whole suite passes.
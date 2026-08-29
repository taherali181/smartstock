.PHONY: check web-check api-test forecast-test openapi platform-integration

check: web-check api-test forecast-test

web-check:
	npm run lint
	npm run build

api-test:
	PYTHONPATH=apps/api python3 -m pytest apps/api/tests

forecast-test:
	PYTHONPATH=services/forecasting python3 -m pytest services/forecasting/tests

openapi:
	npm run generate:api

platform-integration:
	cd apps/api && alembic upgrade head
	SMARTSTOCK_TEST_DATABASE_URL=postgresql+psycopg://smartstock:smartstock@localhost:5432/smartstock SMARTSTOCK_EXTERNAL_SERVICES=1 PYTHONPATH=apps/api python3 -m pytest apps/api/tests -m "postgres or external"

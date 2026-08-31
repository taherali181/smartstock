.PHONY: check web-check api-test forecast-test openapi platform-integration dev stack-status seed-db

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

dev:
	@scripts/devstack.sh start
	@echo "starting api on :8000 and web on :5173 (ctrl-c stops both)"
	@SMARTSTOCK_INVENTORY_BACKEND=postgres \
	 SMARTSTOCK_AUTH_MODE=development \
	 SMARTSTOCK_DATABASE_URL="postgresql+psycopg://smartstock:smartstock@127.0.0.1:5432/smartstock" \
	 PYTHONPATH=apps/api python3 -m uvicorn smartstock_api.main:app --port 8000 --reload & \
	 npm run dev; \
	 kill %1 2>/dev/null || true

stack-status:
	@scripts/devstack.sh status

seed-db:
	@SMARTSTOCK_INVENTORY_BACKEND=postgres \
	 SMARTSTOCK_AUTH_MODE=development \
	 SMARTSTOCK_DATABASE_URL="postgresql+psycopg://smartstock:smartstock@127.0.0.1:5432/smartstock" \
	 PYTHONPATH=apps/api python3 -m smartstock_api.seed

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system smartstock && adduser --system --ingroup smartstock smartstock
COPY pyproject.toml ./
COPY smartstock_api ./smartstock_api
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN pip install --no-cache-dir .
USER smartstock
EXPOSE 8000
CMD ["uvicorn", "smartstock_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

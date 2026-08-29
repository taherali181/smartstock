FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system smartstock && adduser --system --ingroup smartstock smartstock
COPY pyproject.toml ./
COPY smartstock_forecasting ./smartstock_forecasting
RUN pip install --no-cache-dir .
USER smartstock
EXPOSE 8001
CMD ["uvicorn", "smartstock_forecasting.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]

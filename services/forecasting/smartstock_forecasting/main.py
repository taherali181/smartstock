from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from .evaluation import DemandObservation, HistoricMean, Naive, SeasonalNaive

app = FastAPI(title="SmartStock Forecasting", version="0.1.0", redoc_url=None)


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: list[tuple[date, Decimal, bool]] = Field(min_length=1)
    future_dates: list[date] = Field(min_length=1, max_length=365)
    candidate: str


@app.get("/health/live", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/evaluate/baseline")
def baseline(request: ForecastRequest) -> dict[str, object]:
    candidates = {"naive": Naive(), "seasonal_naive": SeasonalNaive(), "historic_mean": HistoricMean()}
    if request.candidate not in candidates:
        return {"status": "unsupported_candidate", "supported": sorted(candidates)}
    history = [DemandObservation(day, demand, censored) for day, demand, censored in request.history]
    predictions = candidates[request.candidate].predict(history, request.future_dates)
    return {
        "candidate": request.candidate,
        "forecasts": [
            {"business_date": item.business_date, "p10": item.p10, "p50": item.p50, "p90": item.p90}
            for item in predictions
        ],
        "warning": "Evaluation only; persisted champion promotion requires an administrator.",
    }

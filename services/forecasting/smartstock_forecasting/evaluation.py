from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from statistics import mean
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class DemandObservation:
    business_date: date
    gross_demand: Decimal
    stockout_censored: bool = False


@dataclass(frozen=True, slots=True)
class QuantileForecast:
    business_date: date
    p10: Decimal
    p50: Decimal
    p90: Decimal

    def __post_init__(self) -> None:
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError("forecast quantiles must be monotonic")


class ForecastModel(Protocol):
    name: str

    def predict(
        self, history: Sequence[DemandObservation], future_dates: Sequence[date]
    ) -> list[QuantileForecast]: ...


class Naive:
    name = "naive"

    def predict(
        self, history: Sequence[DemandObservation], future_dates: Sequence[date]
    ) -> list[QuantileForecast]:
        eligible = [item.gross_demand for item in history if not item.stockout_censored]
        if not eligible:
            raise ValueError("at least one uncensored observation is required")
        level = eligible[-1]
        return [QuantileForecast(day, level, level, level) for day in future_dates]


class SeasonalNaive:
    name = "seasonal_naive"

    def __init__(self, season_length: int = 7) -> None:
        if season_length < 1:
            raise ValueError("season_length must be positive")
        self.season_length = season_length

    def predict(
        self, history: Sequence[DemandObservation], future_dates: Sequence[date]
    ) -> list[QuantileForecast]:
        eligible = [item.gross_demand for item in history if not item.stockout_censored]
        if len(eligible) < self.season_length:
            raise ValueError("history is shorter than the seasonal period")
        season = eligible[-self.season_length :]
        return [
            QuantileForecast(day, season[index % len(season)], season[index % len(season)], season[index % len(season)])
            for index, day in enumerate(future_dates)
        ]


class HistoricMean:
    name = "historic_mean"

    def predict(
        self, history: Sequence[DemandObservation], future_dates: Sequence[date]
    ) -> list[QuantileForecast]:
        eligible = [item.gross_demand for item in history if not item.stockout_censored]
        if not eligible:
            raise ValueError("at least one uncensored observation is required")
        level = Decimal(str(mean(eligible)))
        return [QuantileForecast(day, level, level, level) for day in future_dates]


def wape(actual: Sequence[Decimal], predicted: Sequence[Decimal]) -> Decimal:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted must be nonempty and equally sized")
    denominator = sum((abs(value) for value in actual), Decimal("0"))
    if denominator == 0:
        raise ValueError("WAPE is undefined when aggregate actual demand is zero")
    numerator = sum((abs(a - p) for a, p in zip(actual, predicted, strict=True)), Decimal("0"))
    return numerator / denominator


def interval_coverage(
    actual: Sequence[Decimal], forecasts: Sequence[QuantileForecast]
) -> Decimal:
    if len(actual) != len(forecasts) or not actual:
        raise ValueError("actual and forecasts must be nonempty and equally sized")
    covered = sum(
        1
        for value, forecast in zip(actual, forecasts, strict=True)
        if forecast.p10 <= value <= forecast.p90
    )
    return Decimal(covered) / Decimal(len(actual))


class PromotionDecision(StrEnum):
    ELIGIBLE = "eligible"
    REJECTED_BASELINE = "rejected_baseline"
    REJECTED_CALIBRATION = "rejected_calibration"
    REJECTED_INSUFFICIENT_FOLDS = "rejected_insufficient_folds"


@dataclass(frozen=True, slots=True)
class CandidateScore:
    model_name: str
    fold_wape: tuple[Decimal, ...]
    interval_coverage: Decimal

    @property
    def mean_wape(self) -> Decimal:
        return sum(self.fold_wape, Decimal("0")) / Decimal(len(self.fold_wape))


def evaluate_promotion(
    candidate: CandidateScore,
    seasonal_naive: CandidateScore,
    *,
    minimum_folds: int = 3,
    target_coverage: Decimal = Decimal("0.80"),
    coverage_tolerance: Decimal = Decimal("0.05"),
) -> PromotionDecision:
    if len(candidate.fold_wape) < minimum_folds:
        return PromotionDecision.REJECTED_INSUFFICIENT_FOLDS
    if candidate.mean_wape >= seasonal_naive.mean_wape:
        return PromotionDecision.REJECTED_BASELINE
    if abs(candidate.interval_coverage - target_coverage) > coverage_tolerance:
        return PromotionDecision.REJECTED_CALIBRATION
    return PromotionDecision.ELIGIBLE

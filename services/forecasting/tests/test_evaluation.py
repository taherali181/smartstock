from datetime import date, timedelta
from decimal import Decimal

from smartstock_forecasting.evaluation import (
    CandidateScore,
    DemandObservation,
    PromotionDecision,
    SeasonalNaive,
    evaluate_promotion,
    interval_coverage,
    wape,
)


def test_stockout_censored_days_are_not_zero_demand() -> None:
    start = date(2026, 1, 1)
    history = [
        DemandObservation(start + timedelta(days=i), Decimal(str(value)), censored)
        for i, (value, censored) in enumerate(
            [(1, False), (2, False), (3, False), (4, False), (5, False), (6, False), (7, False), (0, True)]
        )
    ]
    forecast = SeasonalNaive().predict(history, [start + timedelta(days=8)])
    assert forecast[0].p50 == Decimal("1")


def test_metrics_are_exact_decimal_calculations() -> None:
    assert wape([Decimal("10"), Decimal("20")], [Decimal("12"), Decimal("16")]) == Decimal("0.2")


def test_promotion_requires_baseline_win_calibration_and_folds() -> None:
    baseline = CandidateScore(
        "seasonal_naive", (Decimal(".30"), Decimal(".31"), Decimal(".29")), Decimal(".80")
    )
    candidate = CandidateScore(
        "lightgbm_tweedie", (Decimal(".20"), Decimal(".21"), Decimal(".19")), Decimal(".78")
    )
    assert evaluate_promotion(candidate, baseline) == PromotionDecision.ELIGIBLE


def test_interval_coverage_uses_external_p10_p90_contract() -> None:
    start = date(2026, 1, 1)
    forecasts = SeasonalNaive().predict(
        [DemandObservation(start + timedelta(days=i), Decimal("5")) for i in range(7)],
        [start + timedelta(days=7), start + timedelta(days=8)],
    )
    assert interval_coverage([Decimal("5"), Decimal("8")], forecasts) == Decimal("0.5")

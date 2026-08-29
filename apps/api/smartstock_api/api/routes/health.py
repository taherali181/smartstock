from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["health"])


@router.get("/health/live", include_in_schema=False)
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def readiness(request: Request) -> dict[str, object]:
    checks = getattr(request.app.state, "readiness_checks", {})
    results: dict[str, str] = {}
    for name, probe in checks.items():
        try:
            probe()
            results[name] = "ok"
        except Exception:
            results[name] = "unavailable"
    if any(value != "ok" for value in results.values()):
        raise HTTPException(status_code=503, detail={"status": "unavailable", "checks": results})
    return {"status": "ok", "checks": results}

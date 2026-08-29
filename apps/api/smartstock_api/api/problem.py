from fastapi import Request
from fastapi.responses import JSONResponse

from smartstock_api.domain.errors import DomainError


def domain_problem(request: Request, exc: DomainError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://smartstock.example/problems/{exc.code}",
            "title": exc.code.replace("_", " ").title(),
            "status": exc.status_code,
            "detail": str(exc),
            "instance": str(request.url.path),
            "correlation_id": str(correlation_id) if correlation_id else None,
        },
    )

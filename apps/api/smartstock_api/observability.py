from time import perf_counter

from fastapi import FastAPI, Request
from prometheus_client import Counter, Histogram, make_asgi_app

REQUESTS = Counter(
    "smartstock_http_requests_total",
    "HTTP requests",
    ("method", "route", "status"),
)
LATENCY = Histogram(
    "smartstock_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
)


def install_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def metrics(request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(request.method, route).observe(perf_counter() - started)
        return response

    app.mount("/metrics", make_asgi_app())

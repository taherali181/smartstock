from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from smartstock_api.api.problem import domain_problem
from smartstock_api.api.routes import catalog, health, inventory, platform
from smartstock_api.config import get_settings
from smartstock_api.domain.errors import DomainError
from smartstock_api.domain.inventory import InventoryLedger
from smartstock_api.domain.catalog import InMemoryCatalogStore
from smartstock_api.domain.platform import InMemoryPlatformStore, Membership, Organization, Role
from smartstock_api.infrastructure.database import TenantSessionFactory, create_database_engine
from smartstock_api.infrastructure.postgres_catalog import PostgresCatalogStore
from smartstock_api.infrastructure.authorization import PostgresAuthorizationDirectory
from smartstock_api.infrastructure.postgres_inventory import PostgresInventoryStore
from smartstock_api.infrastructure.postgres_platform import PostgresPlatformStore
from smartstock_api.observability import install_observability


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.inventory_backend == "postgres":
        import boto3
        import redis
        from botocore.client import Config
        from kombu import Connection
        from sqlalchemy import text

        engine = create_database_engine(settings)
        sessions = TenantSessionFactory(engine)
        redis_client = redis.Redis.from_url(settings.redis_url)
        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

        def database_probe() -> None:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))

        def rabbitmq_probe() -> None:
            with Connection(settings.broker_url, connect_timeout=2) as connection:
                connection.ensure_connection(max_retries=0)

        app.state.readiness_checks = {
            "postgres": database_probe,
            "redis": redis_client.ping,
            "rabbitmq": rabbitmq_probe,
            "object_storage": lambda: s3_client.head_bucket(Bucket=settings.s3_bucket),
        }
        app.state.inventory_ledger = PostgresInventoryStore(sessions)
        app.state.catalog_store = PostgresCatalogStore(sessions)
        app.state.authorization_directory = PostgresAuthorizationDirectory(sessions)
        app.state.platform_store = PostgresPlatformStore(sessions)
        try:
            yield
        finally:
            redis_client.close()
            engine.dispose()
    else:
        app.state.readiness_checks = {}
        app.state.inventory_ledger = InventoryLedger()
        app.state.catalog_store = InMemoryCatalogStore()
        platform_store = InMemoryPlatformStore()
        organization_id = UUID("00000000-0000-0000-0000-000000000001")
        user_id = UUID("00000000-0000-0000-0000-000000000001")
        platform_store.add_organization(
            Organization(organization_id, "development", "Development Organization", "USD")
        )
        platform_store.add_membership(Membership(organization_id, user_id, Role.OWNER))
        app.state.platform_store = platform_store
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SmartStock API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "X-Correlation-ID",
        ],
        expose_headers=["ETag", "X-Correlation-ID"],
    )
    install_observability(app)

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        supplied = request.headers.get("X-Correlation-ID")
        try:
            request.state.correlation_id = UUID(supplied) if supplied else uuid4()
        except ValueError:
            request.state.correlation_id = uuid4()
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.add_exception_handler(DomainError, domain_problem)
    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(inventory.router)
    app.include_router(platform.router)
    return app


app = create_app()

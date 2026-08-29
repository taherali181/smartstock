import pytest
from pydantic import ValidationError

from smartstock_api.config import Settings


def test_production_rejects_development_security_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            auth_mode="development",
            inventory_backend="memory",
        )


def test_production_accepts_oidc_postgres_and_managed_job_secret() -> None:
    settings = Settings(
        environment="production",
        auth_mode="oidc",
        inventory_backend="postgres",
        job_signing_secret="managed-test-value",
    )
    assert settings.inventory_backend == "postgres"

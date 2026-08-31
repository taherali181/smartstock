from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SMARTSTOCK_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    auth_mode: Literal["oidc", "development"] = "oidc"
    inventory_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+psycopg://smartstock:smartstock@localhost:5432/smartstock"
    redis_url: str = "redis://localhost:6379/0"
    broker_url: str = "amqp://guest:guest@localhost:5672//"
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "smartstock-development"
    s3_region: str = "us-east-1"
    job_signing_secret: SecretStr = SecretStr("development-only-change-me")
    oidc_issuer: AnyHttpUrl = "http://localhost:8080/realms/smartstock"
    oidc_audience: str = "smartstock-api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Conversation model routing (lane `edge`). Inference is local only; the
    # deterministic route needs no model and is always available as a fallback.
    # hybrid: pattern routing first (instant, exact), model only when patterns
    # find nothing. ollama: model first. deterministic: no model at all.
    llm_route: Literal["hybrid", "ollama", "deterministic"] = "hybrid"
    llm_lead_in: bool = False
    ollama_endpoint: str = "http://127.0.0.1:11434"
    ollama_model: str = "granite3.1-moe:3b"
    llm_timeout_seconds: float = 120.0
    llm_keep_alive: str = "2h"

    @model_validator(mode="after")
    def production_safety(self) -> "Settings":
        if self.environment == "production" and self.auth_mode != "oidc":
            raise ValueError("production requires OIDC authentication")
        if self.environment == "production" and self.inventory_backend != "postgres":
            raise ValueError("production requires the PostgreSQL inventory adapter")
        if (
            self.environment == "production"
            and self.job_signing_secret.get_secret_value() == "development-only-change-me"
        ):
            raise ValueError("production requires a managed job signing secret")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

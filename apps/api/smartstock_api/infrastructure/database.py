from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from smartstock_api.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_reset_on_return="rollback",
    )


class TenantSessionFactory:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)

    @contextmanager
    def session(self, organization_id: UUID, user_id: UUID) -> Iterator[Session]:
        with self._sessions.begin() as session:
            # set_config(..., true) is transaction-local. Pool rollback is an
            # additional defense against identity leaking between borrowers.
            session.execute(
                text("SELECT set_config('app.organization_id', :value, true)"),
                {"value": str(organization_id)},
            )
            session.execute(
                text("SELECT set_config('app.user_id', :value, true)"),
                {"value": str(user_id)},
            )
            yield session

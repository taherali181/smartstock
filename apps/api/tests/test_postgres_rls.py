import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from smartstock_api.infrastructure.database import TenantSessionFactory

DATABASE_URL = os.getenv("SMARTSTOCK_TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres


@pytest.fixture
def tenant_database():
    if not DATABASE_URL:
        pytest.skip("SMARTSTOCK_TEST_DATABASE_URL is not configured")
    engine = create_engine(DATABASE_URL, pool_size=1, max_overflow=0, pool_pre_ping=True)
    org_a, org_b, user_a, user_b = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, email_verified)
                VALUES (:a, :email_a, 'Alpha User', true), (:b, :email_b, 'Bravo User', true)
                """
            ),
            {
                "a": user_a,
                "b": user_b,
                "email_a": f"{user_a}@example.test",
                "email_b": f"{user_b}@example.test",
            },
        )
    for organization_id, user_id, slug in (
        (org_a, user_a, f"alpha-{org_a}"),
        (org_b, user_b, f"bravo-{org_b}"),
    ):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :value, true)"),
                {"value": str(organization_id)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO organizations (id, slug, name, currency)
                    VALUES (:id, :slug, :name, 'USD')
                    """
                ),
                {"id": organization_id, "slug": slug, "name": slug},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO memberships (organization_id, user_id, role)
                    VALUES (:organization_id, :user_id, 'owner')
                    """
                ),
                {"organization_id": organization_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feature_flags (organization_id, key, enabled, updated_by)
                    VALUES (:organization_id, 'same-key', true, :user_id)
                    """
                ),
                {"organization_id": organization_id, "user_id": user_id},
            )
    yield engine, org_a, org_b, user_a, user_b
    engine.dispose()


def test_rls_hides_other_tenant_and_blocks_cross_tenant_insert(tenant_database) -> None:
    engine, org_a, org_b, user_a, _ = tenant_database
    sessions = TenantSessionFactory(engine)
    with sessions.session(org_a, user_a) as session:
        visible = session.execute(text("SELECT organization_id FROM feature_flags")).scalars().all()
        assert visible == [org_a]
    with pytest.raises(DBAPIError):
        with sessions.session(org_a, user_a) as session:
            session.execute(
                text(
                    """
                    INSERT INTO feature_flags (organization_id, key, enabled, updated_by)
                    VALUES (:organization_id, 'forged', true, :user_id)
                    """
                ),
                {"organization_id": org_b, "user_id": user_a},
            )


def test_transaction_local_context_is_cleared_on_pool_return(tenant_database) -> None:
    engine, org_a, org_b, user_a, user_b = tenant_database
    sessions = TenantSessionFactory(engine)
    with sessions.session(org_a, user_a) as session:
        assert session.execute(text("SELECT current_setting('app.organization_id')")).scalar() == str(
            org_a
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar() in (None, "")
    with sessions.session(org_b, user_b) as session:
        assert session.execute(text("SELECT count(*) FROM feature_flags")).scalar_one() == 1

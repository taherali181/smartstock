import os

import pytest
from sqlalchemy import create_engine, text

from smartstock_api.seed import DEMO_ORGANIZATION_ID, DEMO_USER_ID, seed_database

DATABASE_URL = os.getenv("SMARTSTOCK_TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres


def test_demo_seed_is_complete_deterministic_and_idempotent() -> None:
    if not DATABASE_URL:
        pytest.skip("SMARTSTOCK_TEST_DATABASE_URL is not configured")

    first = seed_database(DATABASE_URL)
    second = seed_database(DATABASE_URL)

    assert first == second
    assert second["warehouses"] == 3
    assert second["zones"] == 12
    assert second["locations"] == 15
    assert second["products"] == 40
    assert second["suppliers"] == 6
    assert second["customers"] == 8
    assert second["positions"] == 120
    assert second["purchase_orders"] == 2
    assert second["sales_orders"] == 3
    assert second["active_tasks"] >= 4

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            organization = connection.execute(
                text("SELECT slug FROM organizations WHERE id=:id"),
                {"id": DEMO_ORGANIZATION_ID},
            ).scalar_one()
            membership = connection.execute(
                text(
                    """
                    SELECT role FROM memberships
                    WHERE organization_id=:organization_id AND user_id=:user_id
                    """
                ),
                {"organization_id": DEMO_ORGANIZATION_ID, "user_id": DEMO_USER_ID},
            ).scalar_one()
            assert organization == "smartstock-demo"
            assert membership == "owner"
    finally:
        engine.dispose()

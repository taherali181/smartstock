"""Versioned API routes.

Router registration is split by delivery lane so that two agents can add routes
without editing the same block. Append to your own lane's tuple only; see
PARALLEL_PLAN.md section 5.4.
"""

from collections.abc import Iterator

from fastapi import APIRouter

from . import (
    catalog,
    conversations,
    health,
    inventory,
    operations,
    platform,
    proposals,
    reports,
)

# Lane `core`: transactional and numerical truth.
CORE_ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    catalog.router,
    inventory.router,
    operations.router,
    platform.router,
    reports.router,
)

# Lane `edge`: conversation, documents, proposals, integrations.
EDGE_ROUTERS: tuple[APIRouter, ...] = (conversations.router, proposals.router)


def iter_routers() -> Iterator[APIRouter]:
    """Every router the application exposes, in registration order."""
    yield from CORE_ROUTERS
    yield from EDGE_ROUTERS

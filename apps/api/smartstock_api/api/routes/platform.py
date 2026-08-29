from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.domain.platform import ROLE_PERMISSIONS, PlatformStore

router = APIRouter(prefix="/v1", tags=["platform"])


class PlatformModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationResponse(PlatformModel):
    id: str
    slug: str
    name: str
    currency: str
    valuation_method: str


class ApprovalPolicyResponse(PlatformModel):
    id: str
    action_type: str
    minimum_approvals: int
    approver_permission: str
    amount_threshold: str | None
    currency: str | None
    active: bool


class FeatureFlagResponse(PlatformModel):
    key: str
    enabled: bool
    configuration: dict[str, object]


def _store(request: Request) -> PlatformStore:
    return request.app.state.platform_store


@router.get("/organizations/current", response_model=OrganizationResponse)
def current_organization(
    request: Request, principal: Principal = PrincipalDependency
) -> OrganizationResponse:
    organization = _store(request).organization(principal.organization_id)
    return OrganizationResponse(
        id=str(organization.id),
        slug=organization.slug,
        name=organization.name,
        currency=organization.currency,
        valuation_method=organization.valuation_method,
    )


@router.get("/roles")
def roles(principal: Principal = PrincipalDependency) -> dict[str, list[str]]:
    principal.require("administration.manage")
    return {role.value: sorted(permissions) for role, permissions in ROLE_PERMISSIONS.items()}


@router.get("/approval-policies", response_model=list[ApprovalPolicyResponse])
def approval_policies(
    request: Request,
    principal: Principal = PrincipalDependency,
    action_type: Annotated[str | None, Query()] = None,
) -> list[ApprovalPolicyResponse]:
    principal.require("administration.manage")
    return [
        ApprovalPolicyResponse(
            id=str(policy.id),
            action_type=policy.action_type,
            minimum_approvals=policy.minimum_approvals,
            approver_permission=policy.approver_permission,
            amount_threshold=None
            if policy.amount_threshold is None
            else str(policy.amount_threshold),
            currency=policy.currency,
            active=policy.active,
        )
        for policy in _store(request).policies_for(principal.organization_id, action_type)
    ]


@router.get("/feature-flags", response_model=list[FeatureFlagResponse])
def feature_flags(
    request: Request, principal: Principal = PrincipalDependency
) -> list[FeatureFlagResponse]:
    principal.require("administration.manage")
    return [
        FeatureFlagResponse(key=key, enabled=value[0], configuration=value[1])
        for key, value in _store(request).flags_for(principal.organization_id).items()
    ]

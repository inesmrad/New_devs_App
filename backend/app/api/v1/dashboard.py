from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import authenticate_request as get_current_user
from app.services.cache import get_revenue_summary

router = APIRouter()


def _resolve_tenant(user) -> str:
    """The tenant always comes from the authenticated user, never from the request."""
    tenant_id = (
        user.get("tenant_id") if isinstance(user, dict)
        else getattr(user, "tenant_id", None)
    )
    if not tenant_id:
        raise HTTPException(status_code=403, detail="No tenant associated with this user")
    return tenant_id


def _to_money(value: Any) -> str:
    """Round once, at the very end, half-up, and return a string (never a float)."""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    if (month is None) != (year is None):
        raise HTTPException(status_code=400, detail="Provide both month and year, or neither")

    tenant_id = _resolve_tenant(current_user)
    revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)

    return {
        "property_id": revenue_data["property_id"],
        "total_revenue": _to_money(revenue_data["total"]),
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
    }
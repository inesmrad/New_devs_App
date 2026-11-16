import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _run_with_session(fn):
    """Open a DB session from the pool and run `fn(session)`."""
    from app.core.database_pool import DatabasePool

    db_pool = DatabasePool()
    await db_pool.initialize()
    if not db_pool.session_factory:
        raise RuntimeError("Database pool not available")
    async with db_pool.get_session() as session:
        return await fn(session)


async def _monthly_query(
    session, property_id: str, tenant_id: str, month: int, year: int
) -> Tuple[Decimal, int]:
    """Total and booking count for one calendar month in the PROPERTY's local time zone."""
    tz_row = (
        await session.execute(
            text("SELECT timezone FROM properties WHERE id = :pid AND tenant_id = :tid"),
            {"pid": property_id, "tid": tenant_id},
        )
    ).fetchone()

    # Property doesn't exist for this tenant -> nothing to report (never fall back to UTC/other tenants)
    if tz_row is None:
        return Decimal("0"), 0

    tz = ZoneInfo(tz_row.timezone or "UTC")
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=tz)

    row = (
        await session.execute(
            text(
                """
                SELECT COALESCE(SUM(total_amount), 0) AS total, COUNT(*) AS cnt
                FROM reservations
                WHERE property_id = :pid
                  AND tenant_id = :tid
                  AND check_in_date >= :start
                  AND check_in_date < :end
                """
            ),
            {"pid": property_id, "tid": tenant_id, "start": start, "end": end},
        )
    ).fetchone()
    return Decimal(str(row.total)), int(row.cnt)


async def calculate_monthly_revenue(
    property_id: str, tenant_id: str, month: int, year: int, session
) -> Decimal:
    """Revenue for a specific month, using the property's own time zone for month boundaries."""
    total, _ = await _monthly_query(session, property_id, tenant_id, month, year)
    return total


async def calculate_monthly_summary(
    property_id: str, tenant_id: str, month: int, year: int
) -> Dict[str, Any]:
    try:
        total, count = await _run_with_session(
            lambda s: _monthly_query(s, property_id, tenant_id, month, year)
        )
    except Exception as e:
        logger.error(f"Monthly revenue failed for {property_id} (tenant: {tenant_id}): {e}")
        raise HTTPException(status_code=503, detail="Revenue data is temporarily unavailable")

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(total),
        "currency": "USD",
        "count": count,
    }


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """All-time revenue for one property, always scoped to the tenant."""

    async def _query(session):
        result = await session.execute(
            text(
                """
                SELECT
                    property_id,
                    SUM(total_amount) AS total_revenue,
                    COUNT(*) AS reservation_count
                FROM reservations
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                GROUP BY property_id
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        return result.fetchone()

    try:
        row = await _run_with_session(_query)
    except Exception as e:
        # Fail loudly: never show made-up financial numbers to a client.
        logger.error(f"Total revenue failed for {property_id} (tenant: {tenant_id}): {e}")
        raise HTTPException(status_code=503, detail="Revenue data is temporarily unavailable")

    if row:
        total = Decimal(str(row.total_revenue))
        count = int(row.reservation_count)
    else:
        total, count = Decimal("0"), 0

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(total),
        "currency": "USD",
        "count": count,
    }
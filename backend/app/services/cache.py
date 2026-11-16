import json
import logging
import os
from typing import Any, Dict, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _cache_key(tenant_id: str, property_id: str, month: Optional[int], year: Optional[int]) -> str:
    # tenant_id MUST be part of the key: property IDs like "prop-001" exist in several tenants.
    period = f"{year}-{month:02d}" if month and year else "all"
    return f"revenue:{tenant_id}:{property_id}:{period}"


async def get_revenue_summary(
    property_id: str,
    tenant_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    cache_key = _cache_key(tenant_id, property_id, month, year)

    # Try to get from cache (a Redis outage should slow us down, not break the dashboard)
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            # Defence in depth: never serve an entry that belongs to another tenant
            if data.get("tenant_id") == tenant_id:
                return data
    except Exception as e:
        logger.warning(f"Revenue cache read failed: {e}")

    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_monthly_summary, calculate_total_revenue

    if month and year:
        result = await calculate_monthly_summary(property_id, tenant_id, month, year)
    else:
        result = await calculate_total_revenue(property_id, tenant_id)

    # Cache the result for 5 minutes
    try:
        await redis_client.setex(cache_key, 300, json.dumps(result))
    except Exception as e:
        logger.warning(f"Revenue cache write failed: {e}")

    return result
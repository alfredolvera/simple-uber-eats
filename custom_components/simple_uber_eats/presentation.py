"""Pure projections from coordinator data to native entity values."""
from __future__ import annotations

from typing import Any, Mapping


def primary_order(data: Mapping[str, Any]) -> dict[str, Any] | None:
    orders = data.get("orders")
    if not isinstance(orders, list) or not orders or not isinstance(orders[0], dict):
        return None
    return orders[0]


def order_count_attributes(data: Mapping[str, Any]) -> dict[str, int]:
    orders = data.get("orders")
    return {"active_orders_count": len(orders) if isinstance(orders, list) else 0}


def restaurant_attributes(data: Mapping[str, Any]) -> dict[str, float | int]:
    attributes: dict[str, float | int] = order_count_attributes(data)
    order = primary_order(data)
    location = order.get("store_location") if order else None
    if isinstance(location, dict):
        latitude, longitude = location.get("lat"), location.get("lon")
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            attributes.update(latitude=latitude, longitude=longitude)
    return attributes

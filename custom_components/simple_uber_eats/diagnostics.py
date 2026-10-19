"""Only aggregate counts and timings are exportable."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    return {
        "active_order_count": len(snapshot.deliveries) if snapshot else 0,
        "poll_interval_seconds": coordinator.update_interval.total_seconds(),
        "throttle_count": coordinator.throttle_count,
        "successful_polls": coordinator.successful_polls,
        "failed_polls": coordinator.failed_polls,
        "last_poll_duration_seconds": coordinator.last_poll_duration_seconds,
    }

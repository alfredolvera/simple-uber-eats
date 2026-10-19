"""One shared, adaptive cloud poll per configured account."""

import logging
from datetime import UTC, datetime, timedelta
from time import monotonic

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .model import AuthRejected, ServiceUnavailable, Snapshot, Throttled

_LOGGER = logging.getLogger(__name__)


class UberCoordinator(DataUpdateCoordinator[Snapshot]):
    """Keep transport failures distinct from confirmed credential rejection."""

    def __init__(self, hass, entry, gateway):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        self.entry = entry
        self.options_snapshot = dict(entry.options)
        self.gateway = gateway
        self.auth_rejected = False
        self._auth_failures = hass.data.setdefault(DOMAIN, {}).setdefault(
            "http_auth_failures", {}
        )
        self.last_connection_attempt = None
        self.last_connection_success = None
        self.throttle_count = 0
        self.successful_polls = 0
        self.failed_polls = 0
        self.last_poll_duration_seconds = 0.0

    @property
    def _http_auth_failures(self):
        return self._auth_failures.get(self.entry.entry_id, 0)

    @_http_auth_failures.setter
    def _http_auth_failures(self, value):
        if value:
            self._auth_failures[self.entry.entry_id] = min(value, 3)
        else:
            self._auth_failures.pop(self.entry.entry_id, None)

    def persist_credentials(self):
        """Persist validated server rotations even if their response failed."""
        changed = self.gateway.credentials.as_entry_data()
        if any(self.entry.data.get(key) != value for key, value in changed.items()):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, **changed}
            )

    async def _async_update_data(self) -> Snapshot:
        started = monotonic()
        self.last_connection_attempt = datetime.now(UTC).isoformat()
        try:
            result = await self.gateway.read_orders()
        except AuthRejected as error:
            self.failed_polls += 1
            if getattr(error, "http_status", None) in (401, 403):
                self._http_auth_failures += 1
                if self._http_auth_failures < 3:
                    self.auth_rejected = False
                    raise UpdateFailed(
                        "Session check temporarily unsuccessful"
                    ) from None
            self.auth_rejected = True
            raise ConfigEntryAuthFailed("Session no longer accepted") from None
        except Throttled:
            self._http_auth_failures = 0
            self.auth_rejected = False
            self.failed_polls += 1
            self.throttle_count += 1
            seconds = (60, 120, 300)[min(self.throttle_count - 1, 2)]
            self.update_interval = timedelta(seconds=seconds)
            raise UpdateFailed(
                "Service temporarily rate limited", retry_after=seconds
            ) from None
        except (ServiceUnavailable, TimeoutError):
            self._http_auth_failures = 0
            self.auth_rejected = False
            self.failed_polls += 1
            raise UpdateFailed("Service temporarily unavailable") from None
        else:
            self._http_auth_failures = 0
            self.auth_rejected = False
            self.successful_polls += 1
            self.last_connection_success = datetime.now(UTC).isoformat()
            self.throttle_count = 0
            primary = result.primary
            self.update_interval = timedelta(
                seconds=60 if primary is None else 10 if primary.courier_point else 15
            )
            return result
        finally:
            self.last_poll_duration_seconds = round(monotonic() - started, 3)
            self.persist_credentials()

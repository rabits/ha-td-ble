"""The unofficial Transducers Direct BLE devices integration"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import MAX_RETRIES_AFTER_STARTUP
from .coordinator import TDBLEConfigEntry, TDBLEDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


type TDConfigEntry = ConfigEntry[ActiveBluetoothProcessorCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TDConfigEntry) -> bool:
    """Set up Transducers Direct BLE device from a config entry."""
    _LOGGER.debug("Running async_setup_entry")
    coordinator = TDBLEDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    coordinator.td.set_max_attempts(MAX_RETRIES_AFTER_STARTUP)

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: TDBLEConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Running async_unload_entry")
    coord = entry.runtime_data
    await coord.disconnect()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

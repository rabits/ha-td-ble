from __future__ import annotations

from datetime import timedelta
import logging

from .tdlib import TDBluetoothDeviceData, TDDevice
from bleak.backends.device import BLEDevice
from bleak_retry_connector import close_stale_connections_by_address

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_system import METRIC_SYSTEM

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type TDBLEConfigEntry = ConfigEntry[TDBLEDataUpdateCoordinator]


class TDBLEDataUpdateCoordinator(DataUpdateCoordinator[TDDevice]):
    """Class to manage fetching TD BLE data."""

    ble_device: BLEDevice
    config_entry: TDBLEConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TDBLEConfigEntry) -> None:
        """Initialize the coordinator."""
        _LOGGER.debug("Init coordinator")
        self.td = TDBluetoothDeviceData(hass.config.units is METRIC_SYSTEM, persistent=True)
        try:
            super().__init__(
                hass,
                _LOGGER,
                config_entry=entry,
                name=DOMAIN,
                update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            )
        except TypeError:
            # Prior to HA core 2024.11 providing config_entry is not supported
            super().__init__(
                hass,
                _LOGGER,
                name=DOMAIN,
                update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            )
            self.config_entry = entry

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        _LOGGER.debug("Executing Coordinator._async_setup")
        address = self.config_entry.unique_id

        assert address is not None

        await close_stale_connections_by_address(address)

        ble_device = bluetooth.async_ble_device_from_address(self.hass, address)

        if not ble_device:
            _LOGGER.error(f"Could not find TD device with address {address}")
            raise ConfigEntryNotReady(f"Could not find TD device with address {address}")

        self.ble_device = ble_device

    async def _async_update_data(self) -> TDDevice:
        """Get data from TD BLE."""
        _LOGGER.debug("Executing Coordinator._async_update_data")
        if getattr(self, "ble_device", None) is None:
            address = self.config_entry.unique_id
            self.ble_device = bluetooth.async_ble_device_from_address(self.hass, address)
        try:
            data = await self.td.update_device(self.ble_device)
        except Exception as err:
            _LOGGER.error(f"Unable to fetch data: {err}")
            raise UpdateFailed(f"Unable to fetch data: {err}") from err

        return data

    async def disconnect(self):
        if self.td == None:
            _LOGGER.error("Device has no connection")
            return

        await self.td.disconnect()

"""Config flow for Transducers Direct BLE integration."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from bleak import BleakError
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .tdlib import TDBluetoothDeviceData, TD_MANUFACTURER_ID, TD_MANUFACTURER_SERIAL
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class Discovery:
    """A discovered bluetooth device."""

    name: str
    discovery_info: BluetoothServiceInfo
    device: TDDevice


def get_name(device: TDDevice) -> str:
    """Generate name with model and identifier for device."""

    name = device.friendly_name()
    if identifier := device.identifier:
        name += f" ({identifier})"
    return name


class TDDeviceUpdateError(Exception):
    """Custom error class for device updates."""

def is_device_supported(service_info: BluetoothServiceInfo) -> bool:
    """Update from BLE advertisement data."""
    _LOGGER.debug("Parsing TD BLE advertisement data: %s", service_info)
    manufacturer_data = service_info.manufacturer_data
    address = service_info.address
    if TD_MANUFACTURER_ID not in manufacturer_data or manufacturer_data[TD_MANUFACTURER_ID] != TD_MANUFACTURER_SERIAL:
        _LOGGER.debug("Unsupported device '%s' manufacturer data: %s", service_info.name, manufacturer_data)
        return False

    return True

class TDConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TD."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: TDBluetoothDeviceData | None = None
        self._discovered_devices: dict[str, str] = {}

    async def _get_device_data(
        self, discovery_info: BluetoothServiceInfo
    ) -> TDDevice:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, discovery_info.address
        )
        if ble_device is None:
            _LOGGER.debug("No ble_device in _get_device_data")
            raise TDDeviceUpdateError("No ble_device")

        td = TDBluetoothDeviceData()

        try:
            data = await td.update_device(ble_device)
        except BleakError as err:
            _LOGGER.error("Error connecting to and getting data from %s: %s", discovery_info.address, err)
            raise TDDeviceUpdateError("Failed getting device data") from err
        except Exception as err:
            _LOGGER.error("Unknown error occurred from %s: %s", discovery_info.address, err)
            raise

        return data

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered BT device: %s", discovery_info)
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        try:
            device = await self._get_device_data(discovery_info)
        except TDDeviceUpdateError as e:
            _LOGGER.error("Unable to connect to device: %s", e)
            return self.async_abort(reason="cannot_connect")
        except Exception as e:
            _LOGGER.error("Unable to get device data: %s", e)
            return self.async_abort(reason="unknown")

        name = get_name(device)
        self.context["title_placeholders"] = {"name": name}
        self._discovered_device = Discovery(name, discovery_info, device)

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.context["title_placeholders"]["name"], data={}
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            discovery = self._discovered_devices[address]

            self.context["title_placeholders"] = {
                "name": discovery.name,
            }

            self._discovered_device = discovery

            return self.async_create_entry(title=discovery.name, data={})

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue

            if not is_device_supported(discovery_info):
                continue

            try:
                device = await self._get_device_data(discovery_info)
            except TDDeviceUpdateError as e:
                _LOGGER.error("Unable to connect to device: %s", e)
                return self.async_abort(reason="cannot_connect")
            except Exception as e:
                _LOGGER.error("Unable to get device data: %s", e)
                return self.async_abort(reason="unknown")
            name = get_name(device)
            self._discovered_devices[address] = Discovery(name, discovery_info, device)

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        titles = { address:
            discovery.device.name for (address, discovery) in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(titles),
            }),
        )

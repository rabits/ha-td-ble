"""Parser for Transducers Direct BLE advertisements."""

from __future__ import annotations

import struct
import logging

import sys
import asyncio
import dataclasses
from collections import namedtuple
from functools import partial
from typing import Callable, Optional

from async_interrupt import interrupt
from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bleak_retry_connector import close_stale_connections_by_address

if sys.version_info[:2] < (3, 11):
    from async_timeout import timeout as asyncio_timeout
else:
    from asyncio import timeout as asyncio_timeout

from .device_type import TDDeviceType
from .const import (
    TD_MANUFACTURER_ID,
    TD_MANUFACTURER_SERIAL,

    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_MAX_UPDATE_ATTEMPTS,

    CHAR_MODEL_NUMBER,
    CHAR_DEVICE_NAME,
    CHAR_SERIAL_NUMBER,
    CHAR_FIRMWARE_REV,
    CHAR_MANUFACTURER,

    CHAR_PRESSURE,
    CHAR_MAXPRESSURE,
    CHAR_BATTERY,
    CHAR_TEMPERATURE,

    UPDATE_TIMEOUT,
)


_LOGGER = logging.getLogger(__name__)

class DisconnectedError(Exception):
    """Disconnected from device."""

Characteristic = namedtuple("Characteristic", ["uuid", "name", "format"])
device_info_characteristics = [
    Characteristic(CHAR_MANUFACTURER, "manufacturer", "utf-8"),
    Characteristic(CHAR_SERIAL_NUMBER, "serial_nr", "utf-8"),
    Characteristic(CHAR_DEVICE_NAME, "device_name", "utf-8"),
    Characteristic(CHAR_FIRMWARE_REV, "firmware_rev", "utf-8"),
]

sensors_characteristics = [
    CHAR_TEMPERATURE,
    CHAR_BATTERY,
    CHAR_PRESSURE,
    CHAR_MAXPRESSURE,
]

def _decode_attr(
    name: str, format_type: str, scale: float, max_value: Optional[float] = None
) -> Callable[[bytearray], dict[str, float | None | str]]:
    """same as base decoder, but expects only one value.. for real"""

    def handler(raw_data: bytearray) -> dict[str, float | None | str]:
        val = struct.unpack(format_type, raw_data)
        res: float | None = None
        if len(val) == 1:
            res = val[0] * scale
        if res is not None and max_value is not None:
            # Verify that the result is not above the maximum allowed value
            if res > max_value:
                res = None
        data: dict[str, float | None | str] = {name: res}

        _LOGGER.debug("Parsed raw data: 0x%s : %s", raw_data.hex(), res)

        return data

    return handler

sensor_decoders: dict[
    str,
    Callable[[bytearray], dict[str, float | None | str]],
] = {
    CHAR_PRESSURE: _decode_attr(name="pressure", format_type=">h", scale=1.0 / 10.0),
    CHAR_MAXPRESSURE: _decode_attr(name="maxpressure", format_type=">h", scale=1.0 / 10.0),
    CHAR_TEMPERATURE: _decode_attr(name="temperature", format_type=">h", scale=1.0 / 100.0),
    CHAR_BATTERY: _decode_attr(name="battery", format_type="b", scale=1),
}

@dataclasses.dataclass
class TDDeviceInfo:
    """Response data with information about the TD device without sensors."""

    manufacturer: str = ""
    fw_version: str = ""
    model: TDDeviceType = TDDeviceType.UNKNOWN
    name: str = ""
    identifier: str = ""
    address: str = ""
    did_first_sync: bool = False

    def friendly_name(self) -> str:
        """Generate a name for the device."""

        return f"TD {self.model.product_name}"


@dataclasses.dataclass
class TDDevice(TDDeviceInfo):
    """Response data with information about the TD device"""

    sensors: dict[str, str | float | None] = dataclasses.field(
        default_factory=lambda: {}
    )

class TDBluetoothDeviceData:
    """Data for TD BLE sensors."""

    def __init__(
        self,
        is_metric: bool = True,
        max_attempts: int = DEFAULT_MAX_UPDATE_ATTEMPTS,
    ) -> None:
        """Initialize the TD BLE sensor data object."""
        _LOGGER.debug("Created new TDBluetoothDeviceData")
        self.is_metric = is_metric
        self.device_info = TDDeviceInfo()
        self.max_attempts = max_attempts

    def set_max_attempts(self, max_attempts: int) -> None:
        """Set the number of attempts."""
        self.max_attempts = max_attempts

    async def _get_device_characteristics(
        self, client: BleakClient, device: TDDevice
    ) -> None:
        _LOGGER.debug("Executing TDBluetoothDeviceData._get_device_characteristics")
        device_info = self.device_info
        device_info.address = client.address
        did_first_sync = device_info.did_first_sync

        # We need to fetch model to determ what to fetch.
        if not did_first_sync:
            try:
                data = await client.read_gatt_char(CHAR_MODEL_NUMBER)
            except BleakError as err:
                _LOGGER.debug("Get device characteristics exception: %s", err)
                return

            device_info.model = TDDeviceType.from_raw_value(data.decode("utf-8").strip())
            if device_info.model == TDDeviceType.UNKNOWN:
                _LOGGER.warning("Could not map model number to model name, most likely an unsupported device: %s", data.decode("utf-8"))

        for characteristic in device_info_characteristics:
            if did_first_sync and characteristic.name != "firmware_rev":
                # Only the fw_version can change once set, so we can skip the rest.
                continue

            try:
                data = await client.read_gatt_char(characteristic.uuid)
            except BleakError as err:
                _LOGGER.debug("Get device characteristics exception: %s", err)
                continue
            if characteristic.name == "manufacturer":
                device_info.manufacturer = data.decode(characteristic.format)
            elif characteristic.name == "firmware_rev":
                device_info.fw_version = data.decode(characteristic.format)
            elif characteristic.name == "device_name":
                device_info.name = data.decode(characteristic.format)
            elif characteristic.name == "serial_nr":
                identifier = data.decode(characteristic.format)
                # Some devices return `Serial Number` on Mac instead of
                # the actual serial number.
                if identifier != "Serial Number":
                    device_info.identifier = identifier
            else:
                _LOGGER.debug("Characteristics not handled: %s %s", characteristic.name, characteristic.uuid)

        # In some cases the device name will be empty, for example when using a Mac.
        if not device_info.name:
            device_info.name = device_info.friendly_name()

        if device_info.model:
            device_info.did_first_sync = True

        # Copy the cached device_info to device
        for field in dataclasses.fields(device_info):
            name = field.name
            setattr(device, name, getattr(device_info, name))

    async def _get_service_characteristics(
        self, client: BleakClient, device: TDDevice
    ) -> None:
        _LOGGER.debug("Executing TDBluetoothDeviceData._get_service_characteristics")
        svcs = client.services
        sensors = device.sensors
        for service in svcs:
            for characteristic in service.characteristics:
                uuid_str = str(characteristic.uuid)

                if uuid_str in sensors_characteristics and uuid_str in sensor_decoders:
                    _LOGGER.debug("Updating characteristic %s: %s", uuid_str, characteristic)
                    try:
                        data = await client.read_gatt_char(characteristic)
                    except BleakError as err:
                        _LOGGER.debug("Get service characteristics exception: %s", err)
                        continue

                    sensor_data = sensor_decoders[uuid_str](data)
                    sensors.update(sensor_data)

    def _handle_disconnect(
        self, disconnect_future: asyncio.Future[bool], client: BleakClient
    ) -> None:
        """Handle disconnect from device."""
        _LOGGER.debug("Disconnected from %s", client.address)
        if not disconnect_future.done():
            disconnect_future.set_result(True)

    async def update_device(self, ble_device: BLEDevice) -> TDDevice:
        """Connects to the device through BLE and retrieves relevant data"""
        for attempt in range(self.max_attempts):
            _LOGGER.debug("Updating %s (attempt %d)", ble_device.address, attempt)
            is_final_attempt = attempt == self.max_attempts - 1
            try:
                return await self._update_device(ble_device)
            except DisconnectedError:
                if is_final_attempt:
                    raise
                _LOGGER.debug("Unexpectedly disconnected from %s", ble_device.address)
            except BleakError as err:
                if is_final_attempt:
                    raise
                _LOGGER.debug("Bleak error: %s", err)
        raise RuntimeError("Should not reach this point")

    async def _update_device(self, ble_device: BLEDevice) -> TDDevice:
        """Connects to the device through BLE and retrieves relevant data"""
        device = TDDevice()
        loop = asyncio.get_running_loop()
        disconnect_future = loop.create_future()
        await close_stale_connections_by_address(ble_device.address)
        client: BleakClientWithServiceCache = (
            await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.address,
                disconnected_callback=partial(
                    self._handle_disconnect, disconnect_future
                ),
            )
        )
        try:
            async with interrupt(
                disconnect_future,
                DisconnectedError,
                f"Disconnected from {client.address}",
            ), asyncio_timeout(UPDATE_TIMEOUT):
                await self._get_device_characteristics(client, device)
                await self._get_service_characteristics(client, device)
        except BleakError as err:
            if "not found" in str(err):  # In future bleak this is a named exception
                # Clear the char cache since a char is likely
                # missing from the cache
                await client.clear_cache()
            raise
        finally:
            await client.disconnect()

        return device

"""Parser for Transducers Direct BLE advertisements."""

from __future__ import annotations

import logging

from bleak import BleakError, BLEDevice
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
    retry_bluetooth_connection_error,
)

from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from sensor_state_data import SensorDeviceClass, SensorUpdate, Units
from sensor_state_data.enum import StrEnum

from .const import (
    TD_MANUFACTURER_ID,
    TD_MANUFACTURER_SERIAL,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    CHARACTERISTIC_PRESSURE,
    CHARACTERISTIC_BATTERY,
)

class TDSensor(StrEnum):
    PRESSURE = "pressure"
    BATTERY_PERCENT = "battery_percent"
    SIGNAL_STRENGTH = "signal_strength"


_LOGGER = logging.getLogger(__name__)

class TDBluetoothDeviceData(BluetoothData):
    """Data for Transducers Direct BLE sensors."""

    def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        _LOGGER.debug("Parsing TD BLE advertisement data: %s", service_info)
        manufacturer_data = service_info.manufacturer_data
        address = service_info.address
        if TD_MANUFACTURER_ID not in manufacturer_data:
            _LOGGER.debug("Unsupported device '%s' manufacturer data: %s", service_info.name, manufacturer_data)
            return None

        # TODO: Right now device_serial is not used
        if TD_MANUFACTURER_SERIAL in manufacturer_data:
            _LOGGER.debug("Parsing TD sensor: %s", data)
            data = manufacturer_data[TD_MANUFACTURER_SERIAL]
            device_serial = "<NO SERIAL>"
            if 0x00 in data and data.index(0x00) > 1:
                device_serial = str(data[0:data.index(0x00)])

        self.set_device_manufacturer("Transducers Direct, LLC")

        # TODO: add moar supported device types and figure out how to detect them
        self.set_device_type("TDWLB-LC-RPPF")

        name = f"TDWLB-LC-RPPF {short_address(address)}"
        self.set_device_name(name)
        self.set_title(name)

        self.set_precision(2)

    def poll_needed(
        self, service_info: BluetoothServiceInfo, last_poll: float | None
    ) -> bool:
        """
        This is called every time we get a service_info for a device. It means the
        device is working and online.
        """
        if last_poll is None:
            return True
        # TODO: Add a way to change update interval from provided options
        update_interval = DEFAULT_UPDATE_INTERVAL_SECONDS
        return last_poll > update_interval

    @retry_bluetooth_connection_error()
    async def _get_payload(self, client: BleakClientWithServiceCache) -> None:
        """Get the payload from the sensor using its gatt_characteristics."""
        battery_char = client.services.get_characteristic(CHARACTERISTIC_BATTERY)
        battery_payload = await client.read_gatt_char(battery_char)

        pressure_char = client.services.get_characteristic(CHARACTERISTIC_PRESSURE)
        pressure_payload = await client.read_gatt_char(pressure_char)

        self.update_sensor(
            str(TDSensor.PRESSURE),
            Units.PRESSURE_PSI,
            pressure_payload[0],
            SensorDeviceClass.PRESSURE,
            "Pressure",
        )
        self.update_sensor(
            str(TDSensor.BATTERY_PERCENT),
            Units.PERCENTAGE,
            battery_payload[0],
            SensorDeviceClass.BATTERY,
            "Battery",
        )
        _LOGGER.debug("Successfully read active gatt characters")

    async def async_poll(self, ble_device: BLEDevice) -> SensorUpdate:
        """
        Poll the device to retrieve any values we can't get from passive listening.
        """
        _LOGGER.debug("Polling TD device: %s", ble_device.address)
        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, ble_device.address
        )
        try:
            await self._get_payload(client)
        except BleakError as err:
            _LOGGER.warning(f"Reading gatt characters failed with err: {err}")
        finally:
            await client.disconnect()
            _LOGGER.debug("Disconnected from active bluetooth client")
        return self._finish_update()

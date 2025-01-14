"""Transducers Direct BLE sensor"""

from __future__ import annotations

import logging

from .tdlib import TDDevice

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    Platform,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import (
    RegistryEntry,
    async_entries_for_device,
)
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_system import METRIC_SYSTEM

from .const import DOMAIN
from .coordinator import TDBLEConfigEntry, TDBLEDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


SENSORS_MAPPING_TEMPLATE: dict[str, SensorEntityDescription] = {
    "temperature": SensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "pressure": SensorEntityDescription(
        key="pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "maxpressure": SensorEntityDescription(
        key="maxpressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "signal_strength": SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    "battery": SensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


@callback
def async_migrate(hass: HomeAssistant, address: str, sensor_name: str) -> None:
    """Migrate entities to new unique ids (with BLE Address)."""
    _LOGGER.debug("Migrating sensor '%s'", sensor_name)
    ent_reg = er.async_get(hass)
    unique_id_trailer = f"_{sensor_name}"
    new_unique_id = f"{address}{unique_id_trailer}"

    if ent_reg.async_get_entity_id(DOMAIN, Platform.SENSOR, new_unique_id):
        # New unique id already exists
        return

    dev_reg = dr.async_get(hass)

    if not (device := dev_reg.async_get_device(connections={(CONNECTION_BLUETOOTH, address)})):
        return

    entities = async_entries_for_device(ent_reg, device_id=device.id, include_disabled_entities=True)
    matching_reg_entry: RegistryEntry | None = None

    for entry in entities:
        if entry.unique_id.endswith(unique_id_trailer) and \
                (not matching_reg_entry or "(" not in entry.unique_id):
            matching_reg_entry = entry

    if not matching_reg_entry or matching_reg_entry.unique_id == new_unique_id:
        # Already has the newest unique id format
        return

    entity_id = matching_reg_entry.entity_id
    ent_reg.async_update_entity(entity_id=entity_id, new_unique_id=new_unique_id)

    _LOGGER.debug("Migrated entity '%s' to unique id '%s'", entity_id, new_unique_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TDBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TD BLE sensors."""
    _LOGGER.debug("Setup entity")
    is_metric = hass.config.units is METRIC_SYSTEM

    coordinator = entry.runtime_data

    # we need to change some units
    sensors_mapping = SENSORS_MAPPING_TEMPLATE.copy()
    #if not is_metric:
    #    for key, val in sensors_mapping.items():

    entities = []
    _LOGGER.debug("got sensors: %s", coordinator.data.sensors)
    for sensor_type, sensor_value in coordinator.data.sensors.items():
        if sensor_type not in sensors_mapping:
            _LOGGER.debug("Unknown sensor type detected: %s, %s", sensor_type, sensor_value)
            continue
        async_migrate(hass, coordinator.data.address, sensor_type)
        entities.append(TDSensor(coordinator, coordinator.data, sensors_mapping[sensor_type]))

    async_add_entities(entities)


class TDSensor(
    CoordinatorEntity[TDBLEDataUpdateCoordinator], SensorEntity
):
    """TD BLE sensors for the device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TDBLEDataUpdateCoordinator,
        td_device: TDDevice,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Populate the TD entity with relevant data."""
        super().__init__(coordinator)
        self.entity_description = entity_description

        name = td_device.name
        if (identifier := td_device.identifier) in name:
            name = f"{td_device.model.product_name} {identifier}"
        else:
            name += f" ({identifier})"

        self._attr_unique_id = f"{td_device.address}_{entity_description.key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, td_device.address)},
            name=name,
            manufacturer=td_device.manufacturer,
            sw_version=td_device.fw_version,
            model=td_device.model.product_name,
        )

    @property
    def available(self) -> bool:
        """Check if device and sensor is available in data."""
        return (super().available and self.entity_description.key in self.coordinator.data.sensors)

    @property
    def native_value(self) -> StateType:
        """Return the value reported by the sensor."""
        return self.coordinator.data.sensors[self.entity_description.key]

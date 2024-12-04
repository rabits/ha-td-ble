"""Device type of the Transducers Direct device"""

from enum import Enum


class TDDeviceType(Enum):
    """TD device types."""

    UNKNOWN = 0
    PRESSURE_LCR03F = "TDWLB-LCR03F"

    raw_value: str

    def __new__(cls, value: str) -> "TDDeviceType":
        """Create new device type."""
        obj = object.__new__(cls)
        obj.raw_value = value
        return obj

    @classmethod
    def from_raw_value(cls, value: str) -> "TDDeviceType":
        """Get device type from raw value."""
        for device_type in cls:
            if device_type.value == value:
                device_type.raw_value = value
                return device_type
        unknown_device = TDDeviceType.UNKNOWN
        unknown_device.raw_value = value
        return unknown_device

    @property
    def product_name(self) -> str:
        """Get product name."""
        if self == TDDeviceType.PRESSURE_LCR03F:
            return "Pressure LCR03F"
        return "Unknown"

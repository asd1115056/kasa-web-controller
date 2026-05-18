"""Domain exceptions for device operations."""


class DeviceOfflineError(Exception):
    """Device confirmed offline (cannot connect after retries)."""


class DeviceOperationError(Exception):
    """Operation failed but device may still be online."""

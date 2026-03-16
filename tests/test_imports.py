"""Import smoke tests — catch missing/renamed const exports before they reach HA."""


def test_sensor_imports() -> None:
    import custom_components.kilowahti.sensor  # noqa: F401


def test_binary_sensor_imports() -> None:
    import custom_components.kilowahti.binary_sensor  # noqa: F401


def test_coordinator_imports() -> None:
    import custom_components.kilowahti.coordinator  # noqa: F401


def test_config_flow_imports() -> None:
    import custom_components.kilowahti.config_flow  # noqa: F401


def test_services_imports() -> None:
    import custom_components.kilowahti.services  # noqa: F401

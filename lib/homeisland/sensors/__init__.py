"""Optional hardware sensors.

A sensor is any object with `id`, `label` and a `read()` method returning a
dict of values. Sensors are optional: a missing or failing sensor produces an
error entry in the status data and never stops the collector.

To add a sensor type, implement it in this package and register it in
`configured_sensors()`.
"""

from __future__ import annotations

from typing import List

from ..config import Config


def configured_sensors(cfg: Config, enabled: List[str]) -> list:
    sensors = []
    if "bme280" in enabled:
        from .bme280 import BME280

        sensors.append(
            BME280(
                bus=cfg.get_int("HOMEISLAND_BME280_BUS"),
                address=cfg.get_int("HOMEISLAND_BME280_ADDRESS"),
                label=cfg.get("HOMEISLAND_BME280_LABEL"),
            )
        )
    return sensors


def read_all(sensors: list) -> list:
    out = []
    for sensor in sensors:
        entry = {"id": sensor.id, "label": sensor.label, "type": sensor.kind}
        try:
            entry["values"] = sensor.read()
            entry["ok"] = True
        except Exception as exc:  # hardware may be absent or flaky
            entry["ok"] = False
            entry["error"] = str(exc)[:200]
        out.append(entry)
    return out

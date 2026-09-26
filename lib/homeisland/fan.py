"""Temperature-based fan control (experimental, disabled unless the `fan` module is enabled).

The control logic (FanCurve) is independent from the output driver so other
drivers (a fan controller board, an I2C PWM chip, ...) can be added later.

The only driver shipped is SysfsPwm, which uses the kernel PWM interface
(/sys/class/pwm/pwmchipN). On a Raspberry Pi enable it with a device tree
overlay, e.g. `dtoverlay=pwm,pin=18,func=2` in config.txt.

Hardware safety: a GPIO pin can only provide a 3.3 V logic signal. It must
never power a fan. See docs/HARDWARE.md.
"""

from __future__ import annotations

import os
from typing import Optional


class FanCurve:
    """Linear curve between (min_temp, min_duty) and (max_temp, max_duty) with hysteresis.

    Below min_temp the fan runs at min_duty (0 turns it off). Once above
    max_temp it runs at max_duty.
    """

    def __init__(self, min_temp: float, max_temp: float, min_duty: float, max_duty: float, hysteresis: float = 2.0):
        if max_temp <= min_temp:
            raise ValueError("max_temp must be greater than min_temp")
        if not (0 <= min_duty <= max_duty <= 100):
            raise ValueError("duty cycles must satisfy 0 <= min_duty <= max_duty <= 100")
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.min_duty = min_duty
        self.max_duty = max_duty
        self.hysteresis = hysteresis
        self._last_temp: Optional[float] = None
        self._last_duty: Optional[float] = None

    def duty_for(self, temp: float) -> float:
        # Ignore small drops to avoid the fan oscillating around a threshold.
        if self._last_temp is not None and self._last_duty is not None:
            if self._last_temp - self.hysteresis < temp < self._last_temp:
                return self._last_duty
        if temp <= self.min_temp:
            duty = self.min_duty
        elif temp >= self.max_temp:
            duty = self.max_duty
        else:
            span = (temp - self.min_temp) / (self.max_temp - self.min_temp)
            duty = self.min_duty + span * (self.max_duty - self.min_duty)
        duty = round(duty, 1)
        self._last_temp, self._last_duty = temp, duty
        return duty


class SysfsPwm:
    def __init__(self, chip: int = 0, channel: int = 0, period_ns: int = 40000, invert: bool = False,
                 root: str = "/sys/class/pwm"):
        self.chip_path = os.path.join(root, "pwmchip%d" % chip)
        self.channel = channel
        self.path = os.path.join(self.chip_path, "pwm%d" % channel)
        self.period_ns = period_ns
        self.invert = invert
        self._ready = False

    def _write(self, name: str, value: str) -> None:
        with open(os.path.join(self.path, name), "w") as fh:
            fh.write(value)

    def setup(self) -> None:
        if not os.path.isdir(self.chip_path):
            raise RuntimeError("%s not found (enable the PWM overlay)" % self.chip_path)
        if not os.path.isdir(self.path):
            with open(os.path.join(self.chip_path, "export"), "w") as fh:
                fh.write(str(self.channel))
        self._write("period", str(self.period_ns))
        self._write("enable", "1")
        self._ready = True

    def set_duty(self, percent: float) -> None:
        if not self._ready:
            self.setup()
        percent = max(0.0, min(100.0, percent))
        if self.invert:
            percent = 100.0 - percent
        self._write("duty_cycle", str(int(self.period_ns * percent / 100.0)))


class FanController:
    def __init__(self, curve: FanCurve, driver, source: str = "cpu"):
        self.curve = curve
        self.driver = driver
        self.source = source
        self.last: dict = {}

    def update(self, temps: dict) -> dict:
        temp = temps.get(self.source)
        if temp is None:
            # Unknown temperature: fail safe to full speed.
            duty = self.curve.max_duty
            state = {"source": self.source, "temperature_c": None, "duty_pct": duty,
                     "note": "temperature unavailable, running at maximum"}
        else:
            duty = self.curve.duty_for(temp)
            state = {"source": self.source, "temperature_c": temp, "duty_pct": duty}
        try:
            self.driver.set_duty(duty)
            state["ok"] = True
        except Exception as exc:
            state["ok"] = False
            state["error"] = str(exc)[:200]
        self.last = state
        return state

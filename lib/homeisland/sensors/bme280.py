"""Bosch BME280 temperature/humidity/pressure sensor over Linux I2C (/dev/i2c-N).

Pure Python (no smbus dependency). Compensation formulas follow the BME280
datasheet, section 4.2.3 (double precision variants).
"""

from __future__ import annotations

import fcntl
import os
import struct
import time
from typing import Dict

I2C_SLAVE = 0x0703

REG_CHIP_ID = 0xD0
REG_RESET = 0xE0
REG_CTRL_HUM = 0xF2
REG_CTRL_MEAS = 0xF4
REG_CONFIG = 0xF5
REG_DATA = 0xF7
CHIP_ID = 0x60


class Calibration:
    def __init__(self, block1: bytes, h1: int, block2: bytes):
        # 0x88..0x9F: T1..T3, P1..P9
        (self.T1, self.T2, self.T3, self.P1, self.P2, self.P3, self.P4, self.P5, self.P6, self.P7, self.P8,
         self.P9) = struct.unpack("<HhhHhhhhhhhh", block1[:24])
        self.H1 = h1
        # 0xE1..0xE7
        self.H2 = struct.unpack("<h", block2[0:2])[0]
        self.H3 = block2[2]
        e4, e5, e6 = block2[3], block2[4], block2[5]
        h4 = (e4 << 4) | (e5 & 0x0F)
        h5 = (e6 << 4) | (e5 >> 4)
        self.H4 = h4 - 4096 if h4 & 0x800 else h4
        self.H5 = h5 - 4096 if h5 & 0x800 else h5
        self.H6 = struct.unpack("<b", block2[6:7])[0]


def compensate(cal: Calibration, adc_t: int, adc_p: int, adc_h: int) -> Dict[str, float]:
    var1 = (adc_t / 16384.0 - cal.T1 / 1024.0) * cal.T2
    var2 = ((adc_t / 131072.0 - cal.T1 / 8192.0) ** 2) * cal.T3
    t_fine = var1 + var2
    temperature = t_fine / 5120.0

    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * cal.P6 / 32768.0
    var2 = var2 + var1 * cal.P5 * 2.0
    var2 = var2 / 4.0 + cal.P4 * 65536.0
    var1 = (cal.P3 * var1 * var1 / 524288.0 + cal.P2 * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * cal.P1
    if var1 == 0:
        pressure = 0.0
    else:
        p = 1048576.0 - adc_p
        p = (p - var2 / 4096.0) * 6250.0 / var1
        var1 = cal.P9 * p * p / 2147483648.0
        var2 = p * cal.P8 / 32768.0
        pressure = p + (var1 + var2 + cal.P7) / 16.0

    h = t_fine - 76800.0
    if h != 0:
        h = (adc_h - (cal.H4 * 64.0 + cal.H5 / 16384.0 * h)) * (
            cal.H2 / 65536.0 * (1.0 + cal.H6 / 67108864.0 * h * (1.0 + cal.H3 / 67108864.0 * h))
        )
        h = h * (1.0 - cal.H1 * h / 524288.0)
    humidity = max(0.0, min(100.0, h))
    return {
        "temperature_c": round(temperature, 2),
        "humidity_pct": round(humidity, 1),
        "pressure_hpa": round(pressure / 100.0, 1),
    }


class BME280:
    kind = "bme280"

    def __init__(self, bus: int = 1, address: int = 0x76, label: str = "Rack"):
        self.bus = bus
        self.address = address
        self.label = label
        self.id = "bme280-%d-%02x" % (bus, address)
        self._cal = None

    def _open(self) -> int:
        path = "/dev/i2c-%d" % self.bus
        if not os.path.exists(path):
            raise RuntimeError("%s not found (is I2C enabled?)" % path)
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.address)
        except OSError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _read(fd: int, reg: int, length: int) -> bytes:
        os.write(fd, bytes([reg]))
        data = os.read(fd, length)
        if len(data) != length:
            raise RuntimeError("short I2C read")
        return data

    @staticmethod
    def _write(fd: int, reg: int, value: int) -> None:
        os.write(fd, bytes([reg, value]))

    def read(self) -> Dict[str, float]:
        fd = self._open()
        try:
            chip = self._read(fd, REG_CHIP_ID, 1)[0]
            if chip != CHIP_ID:
                raise RuntimeError("unexpected chip id 0x%02x at 0x%02x (BMP280 has no humidity)" % (chip, self.address))
            if self._cal is None:
                self._cal = Calibration(self._read(fd, 0x88, 24), self._read(fd, 0xA1, 1)[0], self._read(fd, 0xE1, 7))
            # Forced mode, oversampling x1 for all channels: one measurement, then sleep.
            self._write(fd, REG_CTRL_HUM, 0x01)
            self._write(fd, REG_CTRL_MEAS, (0x01 << 5) | (0x01 << 2) | 0x01)
            deadline = time.monotonic() + 0.1
            while self._read(fd, 0xF3, 1)[0] & 0x08 and time.monotonic() < deadline:
                time.sleep(0.005)
            time.sleep(0.01)
            raw = self._read(fd, REG_DATA, 8)
        finally:
            os.close(fd)
        adc_p = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        adc_t = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        adc_h = (raw[6] << 8) | raw[7]
        return compensate(self._cal, adc_t, adc_p, adc_h)

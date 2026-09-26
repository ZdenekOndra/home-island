import os
import struct
import tempfile
import unittest

import helpers  # noqa: F401  (sets sys.path)
from helpers import read

from homeisland.fan import FanController, FanCurve, SysfsPwm
from homeisland.sensors.bme280 import Calibration, compensate


class BME280Test(unittest.TestCase):
    def test_datasheet_temperature_example(self):
        # Temperature calibration example from the BME280/BMP280 datasheets.
        block1 = struct.pack("<HhhHhhhhhhhh", 27504, 26435, -1000, 36477, -10685, 3024, 2855, 140, -7, 15500,
                             -14600, 6000)
        block2 = struct.pack("<h", 363) + bytes([0, 0x13, 0x28, 0x03, 30])
        cal = Calibration(block1, 75, block2)
        result = compensate(cal, adc_t=519888, adc_p=415148, adc_h=30000)
        self.assertAlmostEqual(result["temperature_c"], 25.08, places=2)
        self.assertAlmostEqual(result["pressure_hpa"], 1006.5, delta=0.5)
        self.assertGreaterEqual(result["humidity_pct"], 0)
        self.assertLessEqual(result["humidity_pct"], 100)

    def test_signed_humidity_calibration(self):
        block1 = bytes(24)
        # H4/H5 are 12-bit signed values spread over three registers.
        cal = Calibration(block1, 0, struct.pack("<h", 0) + bytes([0, 0xFF, 0xFF, 0xFF, 0]))
        self.assertEqual(cal.H4, -1)
        self.assertEqual(cal.H5, -1)


class FanCurveTest(unittest.TestCase):
    def test_curve(self):
        curve = FanCurve(40, 60, 20, 100, hysteresis=0)
        self.assertEqual(curve.duty_for(30), 20)
        self.assertEqual(curve.duty_for(50), 60)
        self.assertEqual(curve.duty_for(80), 100)

    def test_hysteresis(self):
        curve = FanCurve(40, 60, 0, 100, hysteresis=3)
        self.assertEqual(curve.duty_for(55), 75)
        self.assertEqual(curve.duty_for(54), 75)  # small drop: keep speed
        self.assertEqual(curve.duty_for(50), 50)  # larger drop: follow the curve
        self.assertEqual(curve.duty_for(58), 90)  # rises always follow

    def test_invalid(self):
        with self.assertRaises(ValueError):
            FanCurve(60, 40, 0, 100)
        with self.assertRaises(ValueError):
            FanCurve(40, 60, 50, 20)

    def test_unknown_temperature_fails_safe(self):
        class Recorder:
            duty = None

            def set_duty(self, d):
                self.duty = d

        rec = Recorder()
        state = FanController(FanCurve(40, 60, 20, 90), rec).update({"cpu": None})
        self.assertEqual(rec.duty, 90)
        self.assertIsNone(state["temperature_c"])

    def test_sysfs_driver(self):
        with tempfile.TemporaryDirectory() as root:
            chip = os.path.join(root, "pwmchip0")
            os.makedirs(os.path.join(chip, "pwm0"))
            open(os.path.join(chip, "export"), "w").close()
            pwm = SysfsPwm(0, 0, period_ns=40000, root=root)
            pwm.set_duty(25)
            pwm_file = lambda n: read(os.path.join(chip, "pwm0", n))  # noqa: E731
            self.assertEqual(pwm_file("period"), "40000")
            self.assertEqual(pwm_file("enable"), "1")
            self.assertEqual(pwm_file("duty_cycle"), "10000")
            SysfsPwm(0, 0, period_ns=40000, invert=True, root=root).set_duty(25)
            self.assertEqual(pwm_file("duty_cycle"), "30000")

    def test_sysfs_missing_chip(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                SysfsPwm(3, 0, root=root).set_duty(50)


if __name__ == "__main__":
    unittest.main()

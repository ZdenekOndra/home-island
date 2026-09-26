# Hardware

## Reference system

| Part | Recommendation |
|---|---|
| Board | Raspberry Pi 4 Model B, 8 GB (4 GB is enough without Home Assistant and Jellyfin) |
| System disk | USB 3 SSD, 120–250 GB, booted directly (no SD card in daily use) |
| Data disk | 2.5"/3.5" HDD, size depending on datasets; 3.5" disks need their own power supply |
| Power | Official 5.1 V 3 A USB-C supply; optionally a UPS |
| Network | Wired Gigabit Ethernet |
| Cooling | Case with a heatsink or fan; see below |

HomeIsland also runs on other ARM64 boards (Raspberry Pi 5, many SBCs) and on
x86-64 mini PCs. Only 64-bit systems are supported.

### Power consumption

Typical figures for a Raspberry Pi 4 from public measurements (not measured
by this project): 3–4 W idle, 6–7 W under load, plus 1–2 W for a 2.5" SSD and
3–6 W for a 3.5" HDD (spun up; well under 1 W in standby). A complete idle
system with SSD and sleeping HDD is typically around 5 W.

## BME280 environment sensor (module `bme280`)

Measures temperature, humidity and air pressure in the rack or cabinet.

**Wiring** (3.3 V breakout boards; check your board's labels):

| BME280 | Raspberry Pi (physical pin) |
|---|---|
| VIN / VCC | 3.3 V (pin 1) |
| GND | GND (pin 6) |
| SCL | GPIO 3 / SCL (pin 5) |
| SDA | GPIO 2 / SDA (pin 3) |

Never connect a 5 V supply to a sensor board without a regulator, and never
feed 5 V logic into the Pi's GPIO pins.

**Setup:**

```sh
sudo raspi-config nonint do_i2c 0     # enable I2C, then reboot
sudo apt install i2c-tools
sudo i2cdetect -y 1                   # expect 76 or 77
sudo homeisland module enable bme280
```

If the sensor answers at 0x77, set `HOMEISLAND_BME280_ADDRESS=0x77` in
`/etc/homeisland/homeisland.env` and restart the collector
(`sudo systemctl restart homeisland-collector`). The reading appears in the
dashboard's *Environment* section. A BMP280 (no humidity) is reported as an
error with a clear message.

Place the sensor away from the Pi's own heat, ideally where the air enters the
cabinet.

**Adding other sensors:** implement a class with `id`, `label`, `kind` and
`read()` in `lib/homeisland/sensors/` and register it in
`configured_sensors()`. The dashboard shows any `temperature_c`,
`humidity_pct` and `pressure_hpa` values. Missing or failing sensors never
break the collector.

## UPS monitoring (module `ups`)

HomeIsland reads UPS state from [Network UPS Tools](https://networkupstools.org)
on the host. Minimal USB setup:

```sh
sudo apt install nut
```

`/etc/nut/nut.conf`:
```
MODE=standalone
```

`/etc/nut/ups.conf`:
```
[ups]
    driver = usbhid-ups
    port = auto
    desc = "Home UPS"
```

`/etc/nut/upsd.users` and `/etc/nut/upsmon.conf`: create a monitoring user and
a `MONITOR ups@localhost 1 <user> <password> primary` line (see the NUT
documentation). Then:

```sh
sudo systemctl restart nut-server nut-monitor
upsc ups@localhost
sudo homeisland module enable ups
```

The dashboard shows charge, runtime and whether the UPS is on battery. NUT
itself (`upsmon`) is responsible for shutting the system down when the battery
runs low — configure `SHUTDOWNCMD` and test it.

## Real-time clock (RTC)

A Raspberry Pi 4 has no battery-backed clock, so after a reboot without
Internet the time is wrong (see [OFFLINE_MODE.md](OFFLINE_MODE.md#time-during-long-outages)).
For long disconnected operation, add a DS3231 module (temperature-compensated,
accurate to about a minute per year):

1. Wire it to the I2C pins like the BME280 (it can share the bus).
2. Add `dtoverlay=i2c-rtc,ds3231` to `/boot/firmware/config.txt` and reboot.
3. `sudo apt purge fake-hwclock` (if installed), then check `sudo hwclock -r`.
4. While online and synchronised, write the time: `sudo hwclock -w`.

The Raspberry Pi 5 has a built-in RTC; add the official backup battery.
`homeisland audit` reports whether an RTC is present.

## Cooling and fan control (module `fan`, experimental)

The Raspberry Pi 4 throttles at 80–85 °C. A passive heatsink case is often
enough; in a closed cabinet with disks, forced airflow helps.

### Hardware — read this first

A GPIO pin provides a 3.3 V **signal** of a few milliamps. It must **never**
power a fan directly.

For 12 V PC fans (e.g. Noctua 120 mm):

- **Power**: a separate 12 V supply with enough current (a Noctua NF-A12x25
  draws ~0.14 A). Connect its ground to the Pi's ground.
- **4-pin PWM fans** (recommended): the fan has its own speed-control input
  (25 kHz, open-drain, pulled up inside the fan to ≤ 5 V). Drive it through a
  small NPN transistor or N-channel MOSFET (e.g. 2N7000) as an open-drain
  switch from a GPIO PWM pin. The transistor inverts the signal, so set
  `HOMEISLAND_FAN_INVERT=true`. Alternatively use a ready-made fan driver
  board.
- **3-pin fans** (no PWM input): switching the 12 V supply needs a logic-level
  MOSFET, a flyback diode and preferably a low-frequency PWM; PWM-ing the power
  line of a fan is noisier and can confuse its tachometer. Prefer 4-pin fans.
- Many fans stop below ~20 % duty; set `HOMEISLAND_FAN_MIN_DUTY` accordingly
  (0 lets the fan stop when cool).

If you are not comfortable designing this, use a finished PWM fan controller
HAT or a thermostat-controlled fan hub instead of the `fan` module.

### Software

Enable a hardware PWM channel, e.g. GPIO 18 on a Raspberry Pi 4, in
`/boot/firmware/config.txt`:

```
dtoverlay=pwm,pin=18,func=2
```

After a reboot `/sys/class/pwm/pwmchip0` exists. Configure the curve in
`/etc/homeisland/homeisland.env`:

```
HOMEISLAND_FAN_PWMCHIP=0
HOMEISLAND_FAN_CHANNEL=0
HOMEISLAND_FAN_PERIOD_NS=40000     # 25 kHz, the Intel 4-pin fan specification
HOMEISLAND_FAN_SOURCE=cpu          # cpu, hdd or sensor (BME280)
HOMEISLAND_FAN_MIN_TEMP=45
HOMEISLAND_FAN_MAX_TEMP=70
HOMEISLAND_FAN_MIN_DUTY=20
HOMEISLAND_FAN_MAX_DUTY=100
HOMEISLAND_FAN_INVERT=true         # with a single transistor
```

```sh
sudo homeisland module enable fan
sudo systemctl restart homeisland-collector
```

The collector adjusts the duty cycle every 15 s along a linear curve with 2 °C
hysteresis. It runs the fan at full speed if the temperature cannot be read,
and leaves it at full speed when the collector stops. The dashboard shows the
current duty cycle.

This module is **experimental**: it has been unit-tested against a simulated
sysfs PWM device but not yet on real fan hardware. Watch temperatures after
enabling it.

The code separates the control curve (`FanCurve`) from the output driver
(`SysfsPwm`), so other drivers (I2C PWM chips, fan controller boards) can be
added in `lib/homeisland/fan.py`.

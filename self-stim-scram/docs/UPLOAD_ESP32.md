# “Failed to connect… No serial data received”

Flashing is **USB serial only** (not WiFi/BT).

## 1. Project is already set to slow upload

`platformio.ini` uses **`upload_speed = 115200`**. Rebuild + upload again.

## 2. Force the correct port

In a terminal:

```bash
pio device list
```

Unplug the M5, run again, plug in — whichever **COM** / `/dev/cu.*` appears is your port.

In `platformio.ini` under `[env:m5stack-fire]` uncomment and set:

```ini
upload_port = /dev/cu.usbserial-XXXX
```

(use your exact name; on Mac prefer **`cu.`** not `tty.`)

## 3. Data cable + drivers

- Must be a **data** USB cable.
- **CP210x** (Silicon Labs) or **CH9102** (WCH) — install the driver if no port shows up.

## 4. Nothing else using the port

Quit Serial Monitor, Arduino IDE serial, `screen`, other PIO monitors.

## 5. Manual download mode (if auto-reset fails)

1. **Hold BOOT** (button tied to GPIO0 — on many M5 cores it’s the side **A** or labeled download button; check your Fire v2.7 silkscreen / docs).
2. **Tap RST** (reset) once.
3. **Release BOOT** a second later.
4. Run upload **immediately**:

```bash
pio run -e m5stack-fire-manual -t upload
```

That environment uses `--before=no_reset` so esptool doesn’t fight your manual boot mode.

## 6. Try another USB port / hub

Some hubs and front-panel ports are flaky; use a **direct** motherboard USB-A port if possible.

---

After flash works: **115200** baud serial monitor.

#!/usr/bin/env python3
"""Read-only inventory, no device state changes and no saved credentials."""
from getpass import getpass
from dirigera import Hub
from core import read_config

saved = read_config()
if saved.get("ip") and saved.get("token"):
    ip = saved["ip"]
    token = saved["token"]
    print(f"Using saved DIRIGERA connection ({ip}); read-only discovery.", flush=True)
else:
    ip = input("DIRIGERA IP: ").strip()
    token = getpass("Access token (hidden input): ").strip()
    if not ip or not token:
        raise SystemExit("IP and access token are required")
    print("Connecting (read-only)…", flush=True)
hub = Hub(token=token, ip_address=ip)

print("\n=== LIGHTS (READ-ONLY) ===")
for light in hub.get_lights():
    attrs = light.attributes
    room = getattr(light, "room", None)
    print(f"{getattr(attrs, 'custom_name', 'Unknown')} | id={light.id} | "
          f"room={getattr(room, 'name', 'Unassigned')} | "
          f"room_id={getattr(room, 'id', None)}")

print("\n=== ENVIRONMENT SENSORS (READ-ONLY) ===")
try:
    sensors = hub.get_environment_sensors()
    if not sensors:
        print("None reported via the typed Python API")
    for sensor in sensors:
        attrs = sensor.attributes
        room = getattr(sensor, "room", None)
        print(f"{getattr(attrs, 'custom_name', 'Unknown')} | "
              f"id={sensor.id} | room={getattr(room, 'name', 'Unassigned')} | "
              f"temp_C={getattr(attrs, 'current_temperature', None)} | "
              f"humidity_pct={getattr(attrs, 'current_r_h', None)}")
except Exception as exc:
    print(f"Typed sensor getter failed: {type(exc).__name__}: {exc}")

print("\n=== RAW CLIMATE DEVICES (read-only fallback) ===")
try:
    for raw in hub.get(route="/devices"):
        device_type = str(raw.get("type", ""))
        if "sensor" not in device_type.lower():
            continue
        attrs = raw.get("attributes") or {}
        # Print only non-secret identifying and measurement fields.
        print(f"{device_type} | name={attrs.get('customName')} | "
              f"id={raw.get('id')} | relation_id={raw.get('relationId')} | "
              f"temp_C={attrs.get('currentTemperature')} | "
              f"humidity_pct={attrs.get('currentRH')}")
except Exception as exc:
    print(f"Raw discovery failed: {type(exc).__name__}: {exc}")

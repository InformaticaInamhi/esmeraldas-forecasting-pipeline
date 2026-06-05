# -*- coding: utf-8 -*-
from __future__ import annotations
import requests
from .config_loader import get_float, get_str
from .logger import LOGGER

def get_level_from_thingsboard() -> float | None:
    try:
        host = get_str("THINGSBOARD", "host")
        username = get_str("THINGSBOARD", "username")
        password = get_str("THINGSBOARD", "password")
        device_id = get_str("THINGSBOARD", "device_id")
        key = get_str("THINGSBOARD", "telemetry_key", "h")
        reference = get_float("THINGSBOARD", "sensor_reference_cm", 1282.7)

        r = requests.post(f"{host}/api/auth/login", json={"username": username, "password": password}, timeout=10)
        r.raise_for_status()
        token = r.json().get("token")
        if not token:
            raise RuntimeError("Token no recibido")

        headers = {"X-Authorization": f"Bearer {token}"}
        url = f"{host}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries?keys={key}"
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

        if key not in data or not data[key]:
            raise RuntimeError("Sin datos en ThingsBoard")

        raw = float(data[key][0]["value"])
        level_cm = reference - raw
        if level_cm <= 0 or level_cm >= 800:
            raise RuntimeError("Valor fuera de rango físico")
        level_m = round(level_cm / 100, 3)
        LOGGER.info("INF-TB-001", f"Nivel obtenido desde ThingsBoard: {level_m}")
        return level_m
    except Exception as exc:
        LOGGER.warning("WAR-TB-001", f"Backup ThingsBoard falló: {exc}")
        return None

#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Actualiza nivel observado promedio 3h."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pandas as pd

try:
    from banadih import con_db
except ImportError:
    con_db = None

from main.utils.config_loader import get_int, get_str
from main.utils.connections import create_postgres_connection
from main.utils.logger import LOGGER
from main.utils.thingsboard import get_level_from_thingsboard


def obtener_ciclo_utc() -> datetime:
    ahora_utc = datetime.now(timezone.utc)
    ciclo = (ahora_utc.hour // 3) * 3
    return ahora_utc.replace(hour=ciclo, minute=0, second=0, microsecond=0)


def _query_to_dataframe(sql: str, params_name: str):
    if con_db is None:
        raise ImportError("No se encontró el módulo institucional banadih.con_db")
    return con_db.query_to_dataframe(sql, params=getattr(con_db, params_name))


def obtener_dato_bd() -> tuple[datetime, float | None]:
    fecha_toma = obtener_ciclo_utc()
    tabla_origen = get_str("TABLES", "level_1h_source")
    id_estacion = get_int("GENERAL", "id_estacion_prod")

    horas = [fecha_toma - timedelta(hours=i) for i in [3, 2, 1]]
    q = f"""
        SELECT fecha_toma_dato, "1h"
        FROM {tabla_origen}
        WHERE id_estacion = {id_estacion}
          AND fecha_toma_dato IN ('{horas[0]}', '{horas[1]}', '{horas[2]}')
        ORDER BY fecha_toma_dato;
    """
    df = _query_to_dataframe(q, "paramsHIST")
    if df.empty:
        LOGGER.warning("WAR-L3H-001", "BD sin registros horarios")
        return fecha_toma, None

    valores = pd.to_numeric(df["1h"], errors="coerce").dropna()
    if valores.empty:
        LOGGER.warning("WAR-L3H-002", "BD sin valores válidos")
        return fecha_toma, None

    promedio = round(float(valores.mean()), 3)
    LOGGER.info("INF-L3H-001", f"Promedio 3h desde BD: {promedio}")
    return fecha_toma, promedio


def guardar(fecha_toma: datetime, valor: float) -> None:
    tabla = get_str("TABLES", "forecast_base")
    columna = f'"{get_str("COLUMNS", "level_3h")}"'
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    id_modelo = get_int("GENERAL", "id_modelo_3h")
    id_user = get_int("GENERAL", "id_user")

    sql = f"""
        INSERT INTO {tabla}
        (fecha_toma_dato, id_modelo, id_user, id_estacion, fecha_actualizacion, {columna})
        VALUES (%s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (id_estacion, fecha_toma_dato)
        DO UPDATE SET {columna} = EXCLUDED.{columna}, fecha_actualizacion = NOW();
    """
    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, (fecha_toma, id_modelo, id_user, id_estacion, valor))
        conn.commit()
        LOGGER.info("INF-L3H-999", f"Nivel 3h actualizado: {fecha_toma}={valor}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-L3H-001", f"Error guardando nivel 3h: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    LOGGER.info("INF-L3H-000", "Inicio update_level_3h")
    fecha, valor = obtener_dato_bd()
    if valor is None:
        LOGGER.warning("WAR-L3H-003", "Se intenta backup ThingsBoard")
        valor = get_level_from_thingsboard()
    if valor is None:
        LOGGER.error("ERR-L3H-002", "Proceso abortado sin datos válidos")
        return
    guardar(fecha, valor)


if __name__ == "__main__":
    main()

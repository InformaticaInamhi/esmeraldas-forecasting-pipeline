#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Actualiza nivel promedio 30 minutos y arreglo nivel_30min."""

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


def obtener_posicion() -> tuple[datetime, datetime, int]:
    ahora_utc = datetime.now(timezone.utc)
    ciclo = (ahora_utc.hour // 3) * 3
    ciclo_inicio = ahora_utc.replace(hour=ciclo, minute=0, second=0, microsecond=0)
    minutos = (ahora_utc - ciclo_inicio).total_seconds() / 60
    pos = int(minutos // 30)
    return ahora_utc, ciclo_inicio, pos


def _query_to_dataframe(sql: str, params_name: str):
    if con_db is None:
        raise ImportError("No se encontró el módulo institucional banadih.con_db")
    return con_db.query_to_dataframe(sql, params=getattr(con_db, params_name))


def leer_6_valores(ciclo_inicio: datetime, pos_actual: int) -> list[float | None]:
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    tabla_origen = get_str("TABLES", "level_5min_source")
    end_time = ciclo_inicio + timedelta(minutes=pos_actual * 30)

    valores = []
    for m in [25, 20, 15, 10, 5, 0]:
        t = end_time - timedelta(minutes=m)
        q = f"""
            SELECT fecha_toma_dato, valor
            FROM {tabla_origen}
            WHERE id_estacion = {id_estacion}
              AND fecha_toma_dato BETWEEN '{t - timedelta(minutes=1)}'
                                      AND '{t + timedelta(minutes=1)}'
            ORDER BY ABS(EXTRACT(EPOCH FROM (fecha_toma_dato - '{t}')))
            LIMIT 1;
        """
        df = _query_to_dataframe(q, "paramsRT")
        if df.empty:
            LOGGER.warning("WAR-L30-001", f"No encontrado para t={t}")
            valores.append(None)
        else:
            fv = float(df.iloc[0]["valor"])
            ft = df.iloc[0]["fecha_toma_dato"]
            LOGGER.info("INF-L30-001", f"Dato encontrado {ft} -> {fv}")
            valores.append(fv)
    return valores


def obtener_array_actual(ciclo_inicio: datetime) -> list:
    tabla = get_str("TABLES", "forecast_base")
    col_array = get_str("COLUMNS", "level_30m_array")
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {col_array} FROM {tabla} WHERE id_estacion=%s AND fecha_toma_dato=%s", (id_estacion, ciclo_inicio))
        row = cur.fetchone()
        return list(row[0]) if row and row[0] else [None] * 6
    finally:
        cur.close()
        conn.close()


def upsert(ciclo_inicio: datetime, pos: int, promedio: float, array_final: list) -> None:
    tabla = get_str("TABLES", "forecast_base")
    col_prom = f'"{get_str("COLUMNS", "level_30m")}"'
    col_array = get_str("COLUMNS", "level_30m_array")
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    id_modelo = get_int("GENERAL", "id_modelo_3h")
    id_user = get_int("GENERAL", "id_user")

    sql = f"""
        INSERT INTO {tabla}
        (fecha_toma_dato, id_modelo, id_user, id_estacion, fecha_actualizacion, {col_prom}, {col_array})
        VALUES (%s, %s, %s, %s, NOW(), %s, %s)
        ON CONFLICT (id_estacion, fecha_toma_dato)
        DO UPDATE SET
            {col_prom} = EXCLUDED.{col_prom},
            {col_array} = EXCLUDED.{col_array},
            fecha_actualizacion = NOW();
    """
    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, (ciclo_inicio, id_modelo, id_user, id_estacion, promedio, array_final))
        conn.commit()
        LOGGER.info("INF-L30-999", f"Guardado pos={pos}, promedio={promedio}, array={array_final}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-L30-001", f"Error en upsert nivel 30m: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    ahora_utc, ciclo_inicio, pos = obtener_posicion()
    LOGGER.info("INF-L30-000", f"Inicio update_level_30m. ahora_utc={ahora_utc}, ciclo={ciclo_inicio}, pos={pos}")

    if pos < 0 or pos > 5:
        LOGGER.warning("WAR-L30-002", f"No corresponde ejecutar. pos={pos}")
        return

    valores = leer_6_valores(ciclo_inicio, pos)
    validos = [v for v in valores if v is not None]

    if validos:
        promedio = round(sum(validos) / len(validos), 3)
        LOGGER.info("INF-L30-002", "Fuente usada: BD tiempo real")
    else:
        LOGGER.warning("WAR-L30-003", "BD sin datos. Se usa backup ThingsBoard")
        promedio = get_level_from_thingsboard()
        if promedio is None:
            LOGGER.error("ERR-L30-002", "No hay datos en ninguna fuente")
            return

    array_final = obtener_array_actual(ciclo_inicio)
    array_final[pos] = promedio
    upsert(ciclo_inicio, pos, promedio, array_final)


if __name__ == "__main__":
    main()

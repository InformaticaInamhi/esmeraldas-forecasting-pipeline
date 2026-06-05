#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Actualiza features operacionales desde WRF hacia tabla base del modelo."""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from main.utils.config_loader import get_int, get_str
from main.utils.connections import create_postgres_connection, create_sqlalchemy_engine
from main.utils.logger import LOGGER


COLUMN_MAP = {
    "029030403h": "temperatura_3h",
    "029030412h": "temperatura_6_9_12h",
    "029030472h": "temperatura_15_72h",
    "009010403h": "humedad_relativa_3h",
    "009010412h": "humedad_relativa_6_9_12h",
    "009010472h": "humedad_relativa_15_72h",
    "017140803h": "precipitacion_3h",
    "017140812h": "precipitacion_6_9_12h",
    "017140872h": "precipitacion_15_72h",
}


def obtener_fecha_hoy():
    return datetime.now(ZoneInfo(get_str("GENERAL", "timezone", "America/Guayaquil"))).date()


def procesar_tabla(engine, nombre_tabla: str, alias_var: str) -> pd.DataFrame:
    schema = get_str("TABLES", "wrf_schema")
    id_est_wrf = get_str("WRF_FEATURES", "id_estacion_wrf")
    fecha_hoy = obtener_fecha_hoy()

    query = f"""
        SELECT *
        FROM {schema}.{nombre_tabla}
        WHERE id_estacion = '{id_est_wrf}'
          AND fecha_dato = '{fecha_hoy}'
        ORDER BY fecha_dato;
    """
    df = pd.read_sql(query, engine)
    if df.empty:
        LOGGER.warning("WAR-WRF-001", f"No hay datos WRF hoy ({fecha_hoy}) para {alias_var}")
        return pd.DataFrame()

    columnas = [c for c in df.columns if c.endswith("h")]
    columnas.sort(key=lambda x: int(x[:-1]))
    n_cols = len(columnas)

    resultados = []
    for _, fila in df.iterrows():
        fecha_base = pd.to_datetime(fila["fecha_dato"])
        for paso in range(8):
            idx_start = paso % n_cols
            v3 = fila[columnas[idx_start]]

            cols_6_9_12 = [(idx_start + i) % n_cols for i in range(1, 4)]
            v6_12 = fila[columnas].iloc[cols_6_9_12].mean()

            cols_15_72 = [(idx_start + i) % n_cols for i in range(4, n_cols)]
            v15_72 = fila[columnas].iloc[cols_15_72].mean() if cols_15_72 else pd.NA

            resultados.append({
                "fecha_hora": fecha_base + pd.Timedelta(hours=3 * paso),
                f"{alias_var}_3h": v3,
                f"{alias_var}_6_9_12h": v6_12,
                f"{alias_var}_15_72h": v15_72,
            })

    LOGGER.info("INF-WRF-001", f"Procesados WRF {alias_var}: {len(resultados)} filas")
    return pd.DataFrame(resultados)


def generar_dataframe_wrf() -> pd.DataFrame:
    engine = create_sqlalchemy_engine()
    tablas = {
        "temperatura": get_str("TABLES", "wrf_temp_table"),
        "humedad_relativa": get_str("TABLES", "wrf_humidity_table"),
        "precipitacion": get_str("TABLES", "wrf_precip_table"),
    }

    dfs = []
    for alias, tabla in tablas.items():
        df_temp = procesar_tabla(engine, tabla, alias)
        if not df_temp.empty:
            dfs.append(df_temp)

    if not dfs:
        return pd.DataFrame()

    df_final = dfs[0]
    for df_add in dfs[1:]:
        df_final = df_final.merge(df_add, on="fecha_hora", how="outer")

    return df_final.sort_values("fecha_hora").reset_index(drop=True)


def upsert_wrf(df_final: pd.DataFrame) -> None:
    if df_final.empty:
        LOGGER.warning("WAR-WRF-002", "No se generaron datos WRF")
        return

    tabla = get_str("TABLES", "forecast_base")
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    id_modelo = get_int("GENERAL", "id_modelo_3h")
    id_user = get_int("GENERAL", "id_user")

    cols_bd = list(COLUMN_MAP.keys())
    cols_src = list(COLUMN_MAP.values())
    quoted = [f'"{c}"' for c in cols_bd]
    updates = [f'"{c}" = EXCLUDED."{c}"' for c in cols_bd]

    sql = f"""
        INSERT INTO {tabla}
        (fecha_toma_dato, id_modelo, id_user, id_estacion, fecha_actualizacion, {", ".join(quoted)})
        VALUES (%s, %s, %s, %s, NOW(), {", ".join(["%s"] * len(cols_bd))})
        ON CONFLICT (id_estacion, fecha_toma_dato)
        DO UPDATE SET {", ".join(updates)}, fecha_actualizacion = NOW();
    """

    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        for _, row in df_final.iterrows():
            vals = [row[c] if c in row and pd.notna(row[c]) else None for c in cols_src]
            cur.execute(sql, [pd.to_datetime(row["fecha_hora"]), id_modelo, id_user, id_estacion] + vals)
        conn.commit()
        LOGGER.info("INF-WRF-999", f"Features WRF actualizadas. Filas: {len(df_final)}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-WRF-001", f"Error actualizando WRF features: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    LOGGER.info("INF-WRF-000", "Inicio update_wrf_forecast_features")
    df_final = generar_dataframe_wrf()
    upsert_wrf(df_final)


if __name__ == "__main__":
    main()

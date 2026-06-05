#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Actualiza features de precipitación PERSIANN agregadas por zonas de cuenca."""

from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd

from main.utils.config_loader import get_int, get_project_root, get_str
from main.utils.connections import create_postgres_connection, create_sqlalchemy_engine
from main.utils.logger import LOGGER


def obtener_columna_y_fecha() -> tuple[str, datetime]:
    ahora = datetime.now(ZoneInfo(get_str("GENERAL", "timezone", "America/Guayaquil")))
    ciclo = (ahora.hour // 3) * 3
    col_sat = ciclo - 3
    if col_sat < 0:
        col_sat = 21
    col_hora = f"{col_sat}h"

    if ciclo >= 3:
        fecha_toma = ahora.replace(hour=col_sat, minute=0, second=0, microsecond=0)
    else:
        fecha_toma = (ahora - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)

    LOGGER.info("INF-PERSFEAT-001", f"ciclo={ciclo}, columna={col_hora}, fecha_toma={fecha_toma}")
    return col_hora, fecha_toma


def cargar_estaciones(engine):
    esquema = get_str("TABLES", "persiann_schema")
    tabla_coord = get_str("TABLES", "persiann_coord_table")
    df_est = pd.read_sql(f"SELECT id_estacion, latitud, longitud FROM {esquema}.{tabla_coord};", engine)
    gdf_est = gpd.GeoDataFrame(
        df_est,
        geometry=gpd.points_from_xy(df_est["longitud"], df_est["latitud"]),
        crs="EPSG:4326",
    )
    LOGGER.info("INF-PERSFEAT-002", f"Estaciones virtuales cargadas: {len(gdf_est)}")
    return gdf_est


def cargar_precipitacion(engine, fecha_toma: datetime):
    esquema = get_str("TABLES", "persiann_schema")
    tabla = get_str("TABLES", "persiann_table")
    df = pd.read_sql(
        f"""
        SELECT *
        FROM {esquema}.{tabla}
        WHERE fecha_dato = '{fecha_toma.date()}'
        ORDER BY fecha_dato;
        """,
        engine,
    )
    LOGGER.info("INF-PERSFEAT-003", f"Registros PERSIANN cargados: {len(df)}")
    return df


def cargar_niveles_cuenca() -> dict[int, gpd.GeoDataFrame]:
    root = get_project_root()
    levels_dir = Path(get_str("BASIN", "levels_dir"))
    if not levels_dir.is_absolute():
        levels_dir = root / levels_dir

    pattern = get_str("BASIN", "levels_pattern", "dist_level_{level:02d}.gpkg")
    n_levels = get_int("BASIN", "num_levels", 10)

    niveles = {}
    for nivel in range(1, n_levels + 1):
        ruta = levels_dir / pattern.format(level=nivel)
        if not ruta.exists():
            raise FileNotFoundError(f"No existe archivo de nivel de cuenca: {ruta}")
        niveles[nivel] = gpd.read_file(ruta).to_crs("EPSG:4326")
    return niveles


def calcular_sum_avg(df_prec, gdf_est, col_hora: str) -> dict:
    if col_hora not in df_prec.columns:
        raise ValueError(f"Columna {col_hora} no existe en tabla satelital")

    niveles = cargar_niveles_cuenca()
    asignaciones = []

    for nivel, gdf_nivel in niveles.items():
        join = gpd.sjoin(gdf_est, gdf_nivel, predicate="within")
        join["nivel"] = nivel
        asignaciones.append(join[["id_estacion", "nivel"]])

    df_asig = pd.concat(asignaciones)
    fila = {}

    for nivel in range(1, len(niveles) + 1):
        ids = df_asig[df_asig["nivel"] == nivel]["id_estacion"].unique()
        sub = df_prec[df_prec["id_estacion"].isin(ids)]
        valores = pd.to_numeric(sub[col_hora], errors="coerce")

        fila[f"z{nivel:02d}_sum"] = float(np.nansum(valores))
        media = np.nanmean(valores)
        fila[f"z{nivel:02d}_avg"] = None if np.isnan(media) else float(media)

    LOGGER.info("INF-PERSFEAT-004", "Agregados PERSIANN por cuenca calculados")
    return fila


def guardar_en_produccion(fecha_toma_dato: datetime, fila: dict) -> None:
    tabla = get_str("TABLES", "forecast_base")
    id_estacion = get_int("GENERAL", "id_estacion_prod")
    id_modelo = get_int("GENERAL", "id_modelo_3h")
    id_user = get_int("GENERAL", "id_user")

    columnas = []
    valores = []
    updates = []
    for nivel in range(1, 11):
        for suf in ["sum", "avg"]:
            col = f"z{nivel:02d}_{suf}"
            columnas.append(col)
            valores.append(fila[col])
            updates.append(f"{col} = EXCLUDED.{col}")

    sql = f"""
    INSERT INTO {tabla}
    (fecha_toma_dato, id_modelo, id_user, id_estacion, fecha_actualizacion, {", ".join(columnas)})
    VALUES (%s, %s, %s, %s, NOW(), {", ".join(["%s"] * len(columnas))})
    ON CONFLICT (id_estacion, fecha_toma_dato)
    DO UPDATE SET {", ".join(updates)}, fecha_actualizacion = NOW();
    """

    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, [fecha_toma_dato, id_modelo, id_user, id_estacion] + valores)
        conn.commit()
        LOGGER.info("INF-PERSFEAT-999", f"UPSERT PERSIANN features realizado para {fecha_toma_dato}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-PERSFEAT-001", f"Error guardando PERSIANN features: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    LOGGER.info("INF-PERSFEAT-000", "Inicio update_persiann_basin_features")
    engine = create_sqlalchemy_engine()
    col_hora, fecha_ref = obtener_columna_y_fecha()

    gdf_est = cargar_estaciones(engine)
    df_prec = cargar_precipitacion(engine, fecha_ref)
    if df_prec.empty:
        LOGGER.warning("WAR-PERSFEAT-001", "No hay datos satelitales para procesar")
        return

    fecha_base = pd.to_datetime(df_prec["fecha_dato"].iloc[0])
    hora_real = int(col_hora.replace("h", ""))
    fecha_toma_dato = fecha_base.replace(hour=hora_real, minute=0, second=0, microsecond=0)

    fila = calcular_sum_avg(df_prec, gdf_est, col_hora)
    guardar_en_produccion(fecha_toma_dato, fila)


if __name__ == "__main__":
    main()

#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Ejecuta predicción operacional de nivel a 3 horas."""

from __future__ import annotations
import pickle
from datetime import timedelta

import numpy as np
import pandas as pd

from main.utils.config_loader import get_int, get_str, resolve_path
from main.utils.connections import create_postgres_connection
from main.utils.logger import LOGGER


def cargar_modelo():
    ruta = resolve_path(get_str("MODEL_3H", "model_path"))
    if not ruta.exists():
        raise FileNotFoundError(f"No existe modelo 3h: {ruta}")
    with ruta.open("rb") as f:
        model = pickle.load(f)
    features = model.feature_names_in_.tolist()
    LOGGER.info("INF-FC3-001", f"Modelo 3h cargado con {len(features)} features")
    return model, features


def cargar_datos() -> pd.DataFrame:
    tabla = get_str("TABLES", "forecast_base")
    estacion = get_int("GENERAL", "id_estacion_prod")
    conn = create_postgres_connection()
    try:
        df = pd.read_sql(f"""
            SELECT *
            FROM {tabla}
            WHERE id_estacion = {estacion}
            ORDER BY fecha_toma_dato ASC;
        """, conn)
    finally:
        conn.close()

    df["fecha_toma_dato"] = pd.to_datetime(df["fecha_toma_dato"], utc=True)
    LOGGER.info("INF-FC3-002", f"Registros BD cargados: {len(df)}")
    return df


def preparar_features(df: pd.DataFrame) -> pd.DataFrame:
    target3 = get_str("COLUMNS", "level_3h")
    df = df.sort_values("fecha_toma_dato").copy()

    zones = [f"z{i:02d}_sum" for i in range(1, 11)]
    weights = np.array([0.08,0.12,0.15,0.10,0.05,0.20,0.10,0.05,0.10,0.05])
    weights /= weights.sum()

    for c, w in zip(zones, weights):
        if c in df.columns:
            df[f"{c}_w"] = pd.to_numeric(df[c], errors="coerce") * w

    weighted_cols = [f"{c}_w" for c in zones if f"{c}_w" in df.columns]
    df["zones_weighted_sum"] = df[weighted_cols].sum(axis=1)
    df["zones_weighted_mean"] = df[weighted_cols].mean(axis=1)

    for lag in [3,6,9,12,18,24,48,72]:
        df[f"nivel_lag_{lag}h"] = pd.to_numeric(df[target3], errors="coerce").shift(lag // 3)

    df["nivel_rolling_avg_3"] = df[target3].rolling(3).mean()
    df["nivel_rolling_std_3"] = df[target3].rolling(3).std()
    df["nivel_rolling_avg_6"] = df[target3].rolling(6).mean()
    df["nivel_rolling_std_6"] = df[target3].rolling(6).std()
    df["nivel_roll_12h"] = df[target3].rolling(4).mean()
    df["nivel_roll_24h"] = df[target3].rolling(8).mean()
    df["nivel_roll_48h"] = df[target3].rolling(16).mean()

    df["nivel_diff_3h"] = df[target3].diff(1)
    df["nivel_diff_6h"] = df[target3].diff(2)
    df["nivel_diff_12h"] = df[target3].diff(4)
    return df


def seleccionar_inputs(df: pd.DataFrame, features: list[str]):
    target3 = get_str("COLUMNS", "level_3h")
    target30 = get_str("COLUMNS", "level_30m")

    df_lvl = df.dropna(subset=[target3])
    if df_lvl.empty:
        raise ValueError("No hay nivel 3h observado disponible")

    fila_lvl = df_lvl.iloc[-1]
    base_h = fila_lvl["fecha_toma_dato"]
    destino = base_h + timedelta(hours=3)

    df30 = df[df["fecha_toma_dato"] <= destino].dropna(subset=[target30])
    if df30.empty:
        raise ValueError("No hay nivel 30m disponible")

    x = pd.DataFrame(columns=features, dtype=float)
    x.loc[0] = 0.0
    fallback_count = 0

    for col in features:
        if col not in df.columns:
            fallback_count += 1
            x.at[0, col] = 0.0
            continue

        aux = df[df["fecha_toma_dato"] <= base_h].dropna(subset=[col])
        if len(aux) > 0:
            x.at[0, col] = float(aux.iloc[-1][col])
        else:
            fallback_count += 1
            x.at[0, col] = 0.0

    x.fillna(0.0, inplace=True)
    LOGGER.info("INF-FC3-003", f"Vector 3h construido. base_h={base_h}, destino={destino}, fallback={fallback_count}")
    return x, base_h


def actualizar_pronostico(base_h, pred: float) -> None:
    tabla = get_str("TABLES", "forecast_base")
    estacion = get_int("GENERAL", "id_estacion_prod")
    pred_col = get_str("COLUMNS", "pred_3h")

    sql = f"""
        UPDATE {tabla}
        SET {pred_col} = %s,
            fecha_actualizacion = NOW()
        WHERE id_estacion = %s
          AND fecha_toma_dato = %s;
    """
    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, (float(pred), estacion, base_h))
        updated = cur.rowcount
        conn.commit()
        if updated == 0:
            LOGGER.warning("WAR-FC3-001", f"No existe registro para actualizar pred_3h en {base_h}")
            return
        LOGGER.info("INF-FC3-999", f"Predicción 3h actualizada. fecha={base_h}, pred={pred:.3f}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-FC3-001", f"Error actualizando predicción 3h: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    LOGGER.info("INF-FC3-000", "Inicio run_forecast_3h")
    model, features = cargar_modelo()
    df = cargar_datos()
    df = preparar_features(df)
    x, base_h = seleccionar_inputs(df, features)
    pred = float(model.predict(x)[0])
    LOGGER.info("INF-FC3-004", f"Predicción 3h generada: {pred:.3f}")
    actualizar_pronostico(base_h, pred)


if __name__ == "__main__":
    main()

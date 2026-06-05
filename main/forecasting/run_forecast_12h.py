#!/opt/tljh/user/bin/python3
# -*- coding: utf-8 -*-
"""Ejecuta predicción operacional de nivel a 12 horas."""

from __future__ import annotations
import pickle
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

from main.utils.config_loader import get_float, get_int, get_str, resolve_path
from main.utils.connections import create_postgres_connection
from main.utils.logger import LOGGER

warnings.filterwarnings("ignore")

PRECIP_FEATURES_CERO = [
    "lluvia_media_subcuencas", "lluvia_total_subcuencas", "lluvia_roll_12h",
    "lluvia_roll_24h", "zones_weighted_sum", "wrf_precip_3h",
    "wrf_precip_12h", "wrf_precip_delta_12_3",
] + [f"z{i:02d}_sum" for i in range(1, 11)] + [f"z{i:02d}_sum_w" for i in range(1, 11)]

WRF_FFILL_FEATURES = [
    "wrf_temp_3h", "wrf_temp_12h", "wrf_rh_3h", "wrf_rh_12h",
    "wrf_temp_delta_12_3", "wrf_rh_delta_12_3",
]


def obtener_ciclo_actual_utc() -> pd.Timestamp:
    ahora = pd.Timestamp.utcnow()
    ahora = ahora.tz_localize("UTC") if ahora.tzinfo is None else ahora.tz_convert("UTC")
    hora_ciclo = (ahora.hour // 3) * 3
    return ahora.replace(hour=hora_ciclo, minute=0, second=0, microsecond=0, nanosecond=0)


def cargar_modelo():
    ruta = resolve_path(get_str("MODEL_12H", "model_path"))
    if not ruta.exists():
        raise FileNotFoundError(f"No existe modelo 12h: {ruta}")
    with ruta.open("rb") as f:
        model = pickle.load(f)
    features = model.feature_names_in_.tolist()
    LOGGER.info("INF-FC12-001", f"Modelo 12h cargado con {len(features)} features")
    return model, features


def cargar_datos_base(ciclo_actual_utc: pd.Timestamp) -> pd.DataFrame:
    tabla = get_str("TABLES", "forecast_base")
    estacion = get_int("GENERAL", "id_estacion_prod")
    conn = create_postgres_connection()
    try:
        df = pd.read_sql(
            f"""
            SELECT *
            FROM {tabla}
            WHERE id_estacion = %s
              AND fecha_toma_dato <= %s
            ORDER BY fecha_toma_dato ASC;
            """,
            conn,
            params=(estacion, ciclo_actual_utc.to_pydatetime()),
        )
    finally:
        conn.close()

    if df.empty:
        raise ValueError(f"No existen registros para estación={estacion} hasta {ciclo_actual_utc}")

    df["fecha_toma_dato"] = pd.to_datetime(df["fecha_toma_dato"], utc=True, errors="coerce")
    df = df.sort_values("fecha_toma_dato").reset_index(drop=True)
    LOGGER.info("INF-FC12-002", f"Datos base cargados: {len(df)}")
    return df


def preparar_features_12h(df: pd.DataFrame) -> pd.DataFrame:
    target3 = get_str("COLUMNS", "level_3h")
    target30 = get_str("COLUMNS", "level_30m")
    df = df.sort_values("fecha_toma_dato").copy()

    zone_sum_cols = [f"z{i:02d}_sum" for i in range(1, 11)]
    required = [
        "fecha_toma_dato", "id_estacion", target3, target30,
        "029030403h", "029030412h", "009010403h", "009010412h",
        "017140803h", "017140812h",
    ] + zone_sum_cols

    faltantes = [c for c in required if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas base requeridas: {faltantes}")

    numeric_cols = [target3, target30, "029030403h", "029030412h", "009010403h", "009010412h", "017140803h", "017140812h"] + zone_sum_cols
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    weights = np.array([0.08, 0.12, 0.15, 0.10, 0.05, 0.20, 0.10, 0.05, 0.10, 0.05])
    weights = weights / weights.sum()

    for col, w in zip(zone_sum_cols, weights):
        df[f"{col}_w"] = df[col] * w

    df["zones_weighted_sum"] = df[[f"{c}_w" for c in zone_sum_cols]].sum(axis=1)
    df["lluvia_total_subcuencas"] = df[zone_sum_cols].sum(axis=1)
    df["lluvia_media_subcuencas"] = df[zone_sum_cols].mean(axis=1)

    df["nivel_actual"] = df[target3]
    df["nivel_30m"] = df[target30]

    for lag_h in [3, 6, 12, 24]:
        df[f"nivel_lag_{lag_h}h"] = df[target3].shift(lag_h // 3)

    df["nivel_diff_3h"] = df[target3].diff(1)
    df["nivel_diff_6h"] = df[target3].diff(2)
    df["nivel_diff_12h"] = df[target3].diff(4)
    df["nivel_aceleracion_3_6"] = df["nivel_diff_3h"] - df["nivel_diff_6h"]
    df["nivel_aceleracion_6_12"] = df["nivel_diff_6h"] - df["nivel_diff_12h"]

    df["nivel_roll_mean_12h"] = df[target3].rolling(4).mean()
    df["nivel_roll_mean_24h"] = df[target3].rolling(8).mean()
    df["nivel_roll_std_12h"] = df[target3].rolling(4).std()
    df["nivel_roll_std_24h"] = df[target3].rolling(8).std()

    df["lluvia_roll_12h"] = df["lluvia_total_subcuencas"].rolling(4).sum()
    df["lluvia_roll_24h"] = df["lluvia_total_subcuencas"].rolling(8).sum()

    df["wrf_temp_3h"] = df["029030403h"]
    df["wrf_temp_12h"] = df["029030412h"]
    df["wrf_rh_3h"] = df["009010403h"]
    df["wrf_rh_12h"] = df["009010412h"]
    df["wrf_precip_3h"] = df["017140803h"]
    df["wrf_precip_12h"] = df["017140812h"]
    df["wrf_temp_delta_12_3"] = df["029030412h"] - df["029030403h"]
    df["wrf_rh_delta_12_3"] = df["009010412h"] - df["009010403h"]
    df["wrf_precip_delta_12_3"] = df["017140812h"] - df["017140803h"]

    df["hour"] = df["fecha_toma_dato"].dt.hour
    df["month"] = df["fecha_toma_dato"].dt.month
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    return df


def seleccionar_inputs(df: pd.DataFrame, features: list[str], ciclo_actual_utc: pd.Timestamp):
    df = df.sort_values("fecha_toma_dato").copy()
    ciclo_actual_utc = pd.Timestamp(ciclo_actual_utc)
    ciclo_actual_utc = ciclo_actual_utc.tz_localize("UTC") if ciclo_actual_utc.tzinfo is None else ciclo_actual_utc.tz_convert("UTC")

    faltantes = [c for c in features if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan features requeridas por modelo 12h: {faltantes}")

    fila_ciclo = df[df["fecha_toma_dato"] == ciclo_actual_utc].copy()
    if fila_ciclo.empty:
        raise ValueError(f"No existe fila exacta para ciclo actual {ciclo_actual_utc}")

    nulos = fila_ciclo[features].isna().sum()
    nulos = nulos[nulos > 0].sort_values(ascending=False)

    if not nulos.empty:
        for col in nulos.index:
            if col in PRECIP_FEATURES_CERO:
                fila_ciclo.loc[:, col] = 0.0
            elif col in WRF_FFILL_FEATURES:
                historico = df[(df["fecha_toma_dato"] < ciclo_actual_utc) & (df[col].notna())].sort_values("fecha_toma_dato")
                if historico.empty:
                    raise ValueError(f"No existe histórico válido para imputar {col}")
                fila_ciclo.loc[:, col] = historico.iloc[-1][col]
            else:
                raise ValueError(f"Feature crítica nula en ciclo {ciclo_actual_utc}: {col}")

    fila = fila_ciclo.iloc[0]
    base_h = fila["fecha_toma_dato"]
    destino = base_h + timedelta(hours=12)

    X = pd.DataFrame([fila[features].values], columns=features)
    X = X.apply(pd.to_numeric, errors="coerce")

    if X.isna().any().any():
        raise ValueError(f"Vector final 12h contiene nulos: {X.isna().sum()[X.isna().sum() > 0].to_dict()}")

    LOGGER.info("INF-FC12-003", f"Vector 12h construido. base={base_h}, destino={destino}, shape={X.shape}")
    return X, base_h, destino


def predecir_12h(model, X):
    bias = get_float("MODEL_12H", "bias", 0.0)
    pred_bruta = float(model.predict(X)[0])
    pred_final = pred_bruta - bias
    LOGGER.info("INF-FC12-004", f"Predicción 12h: bruta={pred_bruta:.3f}, bias={bias:.6f}, final={pred_final:.3f}")
    return pred_bruta, pred_final, bias


def obtener_columnas_destino() -> set[str]:
    conn = create_postgres_connection()
    try:
        df_cols = pd.read_sql("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'hm_model_forecast'
              AND table_name = '_014100412h';
        """, conn)
    finally:
        conn.close()
    return set(df_cols["column_name"].tolist())


def guardar_resultado(base_h, destino, pred_final, bias_aplicado) -> None:
    tabla = get_str("TABLES", "forecast_12h")
    estacion = get_int("GENERAL", "id_estacion_prod")
    id_modelo = get_int("GENERAL", "id_modelo_12h")
    id_user = get_int("GENERAL", "id_user")
    cols_destino = obtener_columnas_destino()

    campos, valores, updates = [], [], []

    def agregar(campo, valor, update=True):
        if campo in cols_destino:
            campos.append(campo)
            valores.append(valor)
            if update:
                updates.append(f"{campo} = EXCLUDED.{campo}")

    agregar("fecha_toma_dato", base_h.to_pydatetime(), update=False)
    agregar("id_estacion", estacion, update=False)
    agregar("id_modelo", id_modelo)
    agregar("id_user", id_user)
    agregar("pred_12h", float(pred_final))
    agregar("fecha_prediccion_objetivo", destino.to_pydatetime())
    agregar("bias_12h_aplicado", float(bias_aplicado))

    if "fecha_toma_dato" not in campos or "id_estacion" not in campos:
        raise ValueError("La tabla destino debe tener fecha_toma_dato e id_estacion")
    if "pred_12h" not in campos:
        raise ValueError("La tabla destino debe tener pred_12h")

    sql = f"""
        INSERT INTO {tabla} ({", ".join(campos)})
        VALUES ({", ".join(["%s"] * len(campos))})
        ON CONFLICT (fecha_toma_dato, id_estacion)
        DO UPDATE SET {", ".join(updates) if updates else "fecha_toma_dato = EXCLUDED.fecha_toma_dato"};
    """

    conn = create_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(valores))
        conn.commit()
        LOGGER.info("INF-FC12-999", f"Predicción 12h guardada. base={base_h}, destino={destino}, pred={pred_final:.3f}")
    except Exception as exc:
        conn.rollback()
        LOGGER.error("ERR-FC12-001", f"Error guardando predicción 12h: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def main() -> None:
    LOGGER.info("INF-FC12-000", "Inicio run_forecast_12h")
    ciclo_actual_utc = obtener_ciclo_actual_utc()
    model, features = cargar_modelo()
    df = cargar_datos_base(ciclo_actual_utc)
    df = preparar_features_12h(df)
    X, base_h, destino = seleccionar_inputs(df, features, ciclo_actual_utc)
    _, pred_final, bias_aplicado = predecir_12h(model, X)
    guardar_resultado(base_h, destino, pred_final, bias_aplicado)


if __name__ == "__main__":
    main()

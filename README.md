# Esmeraldas Forecasting Pipeline

Repositorio operativo para la preparación de datos y ejecución de modelos de predicción hidrológica en la cuenca del río Esmeraldas.

## Objetivo

Centralizar los procesos automatizados que actualizan datos observados, variables hidrometeorológicas, features derivadas y predicciones del modelo de nivel para la estación San Mateo.

## Estructura

```text
esmeraldas-forecasting-pipeline/
├── config.ini
├── config.example.ini
├── requirements.txt
├── README.md
├── .gitignore
├── data/
│   └── basin_levels/
│       ├── .gitkeep
│       ├── dist_level_01.gpkg
│       └── ...
├── models/
│   ├── .gitkeep
│   ├── random_forest_esmeraldas.pkl
│   └── modelo_12h_depurado.pkl
└── main/
    ├── ingestion/
    │   ├── update_level_30m.py
    │   └── update_level_3h.py
    ├── features/
    │   ├── update_persiann_basin_features.py
    │   └── update_wrf_forecast_features.py
    ├── forecasting/
    │   ├── run_forecast_3h.py
    │   └── run_forecast_12h.py
    ├── utils/
    │   ├── config_loader.py
    │   ├── connections.py
    │   ├── logger.py
    │   └── thingsboard.py
    └── logs/
```

## Procesos y cron sugerido

Los procesos se ejecutan de forma independiente porque no todos dependen del mismo horario operativo.

```bash
# Nivel 30 minutos, cada 30 minutos
*/30 * * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/ingestion/update_level_30m.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/update_level_30m.log 2>&1

# Nivel observado 3h
5 */3 * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/ingestion/update_level_3h.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/update_level_3h.log 2>&1

# Features PERSIANN por cuenca
15 */3 * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/features/update_persiann_basin_features.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/update_persiann_basin_features.log 2>&1

# Features WRF, aislado por disponibilidad del modelo
10 4 * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/features/update_wrf_forecast_features.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/update_wrf_forecast_features.log 2>&1

# Modelo 3h
25 */3 * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/forecasting/run_forecast_3h.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/run_forecast_3h.log 2>&1

# Modelo 12h
35 */3 * * * /opt/tljh/user/bin/python3 /ruta/esmeraldas-forecasting-pipeline/main/forecasting/run_forecast_12h.py >> /ruta/esmeraldas-forecasting-pipeline/main/logs/run_forecast_12h.log 2>&1
```

## Configuración

Copiar:

```bash
cp config.example.ini config.ini
```

Luego editar `config.ini` con credenciales reales.

## Insumos manuales

Colocar manualmente:

```text
models/random_forest_esmeraldas.pkl
models/modelo_12h_depurado.pkl
data/basin_levels/dist_level_01.gpkg
...
data/basin_levels/dist_level_10.gpkg
```

Por defecto estos archivos no se suben a GitHub.

## Logs

Todos los scripts usan formato ELF:

```text
Fecha y hora | Tipo | IP | Código | Mensaje | Usuario | Contexto
```

## Instalación

```bash
pip install -r requirements.txt
```

# -*- coding: utf-8 -*-
from __future__ import annotations
import psycopg2
from sqlalchemy import create_engine
from .config_loader import get_int, get_str
from .logger import LOGGER

def get_db_config() -> dict:
    return {
        "host": get_str("POSTGRES", "host"),
        "port": get_int("POSTGRES", "port", 5432),
        "dbname": get_str("POSTGRES", "database"),
        "user": get_str("POSTGRES", "user"),
        "password": get_str("POSTGRES", "password"),
    }

def create_postgres_connection():
    cfg = get_db_config()
    conn = psycopg2.connect(**cfg)
    LOGGER.info("INF-CONN-001", f"Conexión PostgreSQL establecida con {cfg['host']}:{cfg['port']}")
    return conn

def create_sqlalchemy_engine():
    cfg = get_db_config()
    return create_engine(f"postgresql://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

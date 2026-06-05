# -*- coding: utf-8 -*-
from __future__ import annotations
import configparser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.ini"

def get_project_root() -> Path:
    return PROJECT_ROOT

def load_config() -> configparser.ConfigParser:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"No se encontró config.ini en: {CONFIG_PATH}")
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    return config

def get_str(section: str, option: str, default: str | None = None) -> str:
    config = load_config()
    if config.has_option(section, option):
        return config.get(section, option)
    if default is not None:
        return default
    raise KeyError(f"No existe [{section}] {option} en config.ini")

def get_int(section: str, option: str, default: int | None = None) -> int:
    return int(get_str(section, option, None if default is None else str(default)))

def get_float(section: str, option: str, default: float | None = None) -> float:
    return float(get_str(section, option, None if default is None else str(default)))

def resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

"""Loads config/config.yaml and applies .env / environment overrides."""
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]  # project folder (this file lives in security/)


@lru_cache(maxsize=1)
def get_settings() -> dict:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    cfg["llm"]["base_url"] = os.getenv("OLLAMA_BASE_URL", cfg["llm"]["base_url"])
    cfg["llm"]["model"] = os.getenv("OLLAMA_MODEL", cfg["llm"]["model"])

    env_all = os.getenv("ALLOW_ALL_READS")
    if env_all is not None and env_all.strip():
        cfg["filesystem"]["allow_all_reads"] = env_all.strip().lower() in ("1", "true", "yes", "on")

    modify = cfg.setdefault("modify", {})
    for env_name, key in (("ALLOW_ALL_WRITES", "allow_all_writes"), ("CONFIRM_CHANGES", "confirm_changes"),
                          ("MODIFY_ENABLED", "enabled")):
        value = os.getenv(env_name)
        if value is not None and value.strip():
            modify[key] = value.strip().lower() in ("1", "true", "yes", "on")

    env_dirs = os.getenv("APPROVED_READ_DIRS")
    if env_dirs:
        cfg["filesystem"]["approved_read_dirs"] = [d.strip() for d in env_dirs.split(";") if d.strip()]
    return cfg
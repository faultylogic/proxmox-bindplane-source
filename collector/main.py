import json
import logging
import os
import time
from typing import Any, Dict

import yaml

from collector.proxmox_client import ProxmoxClient
from collector.metrics import ProxmoxMetricsCollector, setup_meter_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


DEFAULT_CONFIG: Dict[str, Any] = {
    "proxmox": {
        "host": "192.168.1.1",
        "port": 8006,
        "username": "monitoring@pam",
        "token_name": "dynatrace",
        "token_value": "",
        "verify_ssl": False,
    },
    "otlp": {
        "endpoint": "http://192.168.1.190:4317",
        "insecure": True,
        "headers": {},
    },
    "collector": {
        "interval_seconds": 60,
        "service_name": "proxmox-collector",
        "service_version": "1.0.0",
    },
}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge override into base (modifies base in place)."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load config from YAML file, then apply env var overrides."""
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)

    # Load config.yaml if present
    if os.path.exists(config_path):
        with open(config_path) as f:
            file_cfg = yaml.safe_load(f) or {}
        _deep_merge(config, file_cfg)
        logger.info("Loaded config from %s", config_path)
    else:
        logger.info("No config file found at %s — using defaults and env vars", config_path)

    # Env var overrides
    env_map = {
        "PROXMOX_HOST":               ("proxmox", "host"),
        "PROXMOX_PORT":               ("proxmox", "port"),
        "PROXMOX_USERNAME":           ("proxmox", "username"),
        "PROXMOX_TOKEN_NAME":         ("proxmox", "token_name"),
        "PROXMOX_TOKEN_VALUE":        ("proxmox", "token_value"),
        "PROXMOX_VERIFY_SSL":         ("proxmox", "verify_ssl"),
        "OTLP_ENDPOINT":              ("otlp", "endpoint"),
        "OTLP_INSECURE":              ("otlp", "insecure"),
        "OTLP_HEADERS":               ("otlp", "headers"),
        "COLLECTION_INTERVAL_SECONDS": ("collector", "interval_seconds"),
        "SERVICE_NAME":               ("collector", "service_name"),
        "SERVICE_VERSION":            ("collector", "service_version"),
    }

    for env_key, (section, field) in env_map.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        # Type coercion based on existing default type
        existing = config[section][field]
        if isinstance(existing, bool):
            config[section][field] = val.lower() in ("1", "true", "yes")
        elif isinstance(existing, int):
            config[section][field] = int(val)
        elif isinstance(existing, dict):
            # Expect JSON string for dict fields (e.g. OTLP_HEADERS)
            config[section][field] = json.loads(val)
        else:
            config[section][field] = val

    return config


def main():
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    config = load_config(config_path)

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    logger.info(
        "Starting proxmox-collector: host=%s endpoint=%s interval=%ss",
        config["proxmox"]["host"],
        config["otlp"]["endpoint"],
        config["collector"]["interval_seconds"],
    )

    meter = setup_meter_provider(config)

    client = ProxmoxClient(
        host=config["proxmox"]["host"],
        port=int(config["proxmox"]["port"]),
        username=config["proxmox"]["username"],
        token_name=config["proxmox"]["token_name"],
        token_value=config["proxmox"]["token_value"],
        verify_ssl=bool(config["proxmox"]["verify_ssl"]),
    )

    collector = ProxmoxMetricsCollector(client, meter)
    interval = int(config["collector"]["interval_seconds"])

    while True:
        try:
            collector.collect()
            logger.info("Collection cycle complete")
        except Exception as e:
            logger.exception("Collection error: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()

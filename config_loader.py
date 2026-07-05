import yaml
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

config = load_config()

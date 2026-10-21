import json
from pathlib import Path


def normalize_path(value: str) -> str:
    return str(Path(value))

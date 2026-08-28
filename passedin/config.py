"""Config loading. One YAML file drives everything (see config.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml


class Config:
    def __init__(self, raw: dict, base_dir: Path, path: Path | None = None):
        self.raw = raw
        self.base_dir = base_dir
        # Kept so the settings panel can write the criteria back to the very
        # file this was loaded from, rather than guessing at its location.
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).resolve()
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls(raw, path.parent, path)

    def reload(self) -> None:
        """Re-read the file after the settings panel has written to it."""
        if self.path is None:
            return
        with open(self.path) as f:
            self.raw = yaml.safe_load(f)

    def get(self, dotted: str, default=None):
        """config.get("fetch.chrome.headless") style access."""
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # --- resolved paths -----------------------------------------------------
    @property
    def data_dir(self) -> Path:
        p = self.base_dir / self.get("run.data_dir", "./data")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.get("run.db_file", "passedin.sqlite")

    @property
    def cache_dir(self) -> Path:
        p = self.data_dir / self.get("run.cache_subdir", "cache")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = self.data_dir / self.get("run.log_subdir", "logs")
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_secrets(base_dir: Path) -> None:
    """Load KEY=VALUE lines from secrets.env into the environment, if present.

    Session material for fetchers (e.g. BROWSERLESS_API_TOKEN) lives there,
    gitignored, per §11.
    """
    secrets = base_dir / "secrets.env"
    if not secrets.exists():
        return
    for line in secrets.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

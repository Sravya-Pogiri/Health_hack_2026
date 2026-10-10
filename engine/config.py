"""Loads config.yaml. Every threshold the engine uses comes from here."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class Config(dict):
    """A dict with dotted-path lookup: cfg.get_path("routing.cooldown_minutes")."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def with_overrides(self, overrides: dict) -> "Config":
        merged = copy.deepcopy(dict(self))
        _deep_merge(merged, overrides)
        return Config(merged)


def _deep_merge(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def resolve_path(cfg: Config, dotted: str) -> Path:
    """Resolve a path from config relative to the repo root."""
    p = Path(cfg.get_path(dotted))
    return p if p.is_absolute() else REPO_ROOT / p

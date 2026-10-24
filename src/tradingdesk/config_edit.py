"""Changing settings by dotted path ("scanner.min_score"), both in memory and in the YAML file.

The Learning Agent describes every suggestion as a list of such changes. In memory they are used to backtest the
suggestion; in the file they become the pull request you approve. The file is edited with ruamel.yaml, which
keeps every comment and the layout, so the pull request shows exactly the lines that change and nothing else.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tradingdesk.config import Settings


@dataclass
class Change:
    path: str        # e.g. "scanner.setups.mean_reversion.enabled"
    old: Any
    new: Any

    def to_dict(self) -> dict:
        return asdict(self)


def get_path(d: dict, path: str) -> Any:
    cur: Any = d
    for k in path.split("."):
        cur = cur[k]
    return cur


def set_path(d: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value


def with_changes(settings: Settings, changes: list[Change]) -> Settings:
    """A copy of the settings with the changes applied (validated like the file would be)."""
    raw = copy.deepcopy(settings.model_dump(mode="python"))
    for c in changes:
        set_path(raw, c.path, copy.deepcopy(c.new))
    return Settings.model_validate(raw)


def apply_to_file(path: str | Path, changes: list[Change]) -> list[str]:
    """Edit the YAML file in place, keeping comments. Returns a line per change made."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    p = Path(path)
    data = yaml.load(p.read_text())
    done = []
    for c in changes:
        current = get_path(data, c.path)
        if _plain(current) != _plain(c.old):
            done.append(f"skipped {c.path}: it is now {_plain(current)!r}, not {c.old!r} as when the suggestion was made")
            continue
        if isinstance(current, list) and isinstance(c.new, list):
            current[:] = c.new  # keeps the list's flow style ([a, b]) and its comment
        else:
            set_path(data, c.path, c.new)
        done.append(f"{c.path}: {c.old!r} -> {c.new!r}")
    with open(p, "w") as f:
        yaml.dump(data, f)
    # Settings must still load after the edit; anything else is a bug worth stopping for.
    from tradingdesk.config import load_settings

    load_settings(p)
    return done


def _plain(v: Any) -> Any:
    """ruamel's commented containers and scalars as plain Python values, for comparing."""
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int | float):
        return float(v)
    return str(v) if v is not None else None

"""Dataset files. Telemetry and ground truth are written to SEPARATE files on purpose."""

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path

from contracts.scenarios import Episode
from contracts.telemetry import Telemetry

from .config import SimConfig


def write_dataset(out: Path, cfg: SimConfig, episodes: list[Episode], telemetry: Iterable[Telemetry]) -> int:
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with (out / "telemetry.jsonl").open("w", encoding="utf-8") as f:
        for t in telemetry:
            f.write(t.model_dump_json() + "\n")
            n += 1
    with (out / "episodes.jsonl").open("w", encoding="utf-8") as f:
        for e in episodes:
            f.write(e.model_dump_json() + "\n")
    (out / "config.json").write_text(json.dumps(asdict(cfg), default=str, indent=2), encoding="utf-8")
    return n


def read_telemetry(path: Path) -> Iterator[Telemetry]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            yield Telemetry.model_validate_json(line)


def read_episodes(path: Path) -> list[Episode]:
    with path.open(encoding="utf-8") as f:
        return [Episode.model_validate_json(line) for line in f]

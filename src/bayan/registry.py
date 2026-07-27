"""Format Registry — банкны форматыг кодоор биш YAML descriptor-оор тодорхойлно (§2).

Descriptor бүр fingerprint (оноогоор таних), metadata_block, table mapping,
validation тохиргоотой. Шинэ банк = шинэ YAML файл, кодын өөрчлөлтгүй.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGISTRY_DIR = Path(__file__).resolve().parents[2] / "registry"


@dataclass
class ColumnSpec:
    aliases: list[str] = None   # толгойтой формат: нэрээр олно
    index: int | None = None    # толгойгүй формат: баганын дугаараар (0-ээс)
    type: str = "text"          # text | amount
    format: str | None = None   # огнооны формат(ууд), '|'-ээр
    optional: bool = False


@dataclass
class Descriptor:
    id: str
    bank: str
    channel: str
    file_type: str
    status: str
    fingerprint: dict
    metadata_block: dict
    table: dict
    validation: dict
    columns: dict[str, ColumnSpec] = field(default_factory=dict)

    @property
    def direction_mode(self) -> str:
        return self.table.get("direction_mode", "separate")

    @property
    def amount_locale(self) -> dict:
        return self.table.get("amount_locale", {"thousands": ",", "decimal": "."})


def load_descriptor(path: Path) -> Descriptor:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cols = {
        name: ColumnSpec(
            aliases=spec.get("aliases", []),
            index=spec.get("index"),
            type=spec.get("type", "text"),
            format=spec.get("format"),
            optional=spec.get("optional", False),
        )
        for name, spec in raw.get("table", {}).get("columns", {}).items()
    }
    return Descriptor(
        id=raw["id"], bank=raw["bank"], channel=raw.get("channel", ""),
        file_type=raw.get("file_type", ""), status=raw.get("status", "draft"),
        fingerprint=raw.get("fingerprint", {}),
        metadata_block=raw.get("metadata_block", {}),
        table=raw.get("table", {}), validation=raw.get("validation", {}),
        columns=cols,
    )


def load_registry(directory: Path = REGISTRY_DIR,
                  include_draft: bool = True) -> list[Descriptor]:
    descriptors = []
    if directory.exists():
        for f in sorted(directory.glob("*.yaml")):
            d = load_descriptor(f)
            if d.status == "active" or (include_draft and d.status == "draft"):
                descriptors.append(d)
    return descriptors


def fingerprint_score(desc: Descriptor, *, filename: str,
                      cell_texts: list[str]) -> int:
    """Descriptor файлд хэр таарч буйг оноогоор хэмжинэ (§2.1).

    cell_texts: файлын эхний ~40 мөрийн бүх нүдний текст.
    """
    import re
    fp = desc.fingerprint
    score = 0
    blob = " | ".join(cell_texts)

    pattern = fp.get("filename_pattern")
    if pattern and re.search(pattern, filename, flags=re.IGNORECASE):
        score += 1

    for kw in fp.get("sheet_keywords", []):
        if kw.lower() in blob.lower():
            score += 3

    sig = fp.get("column_signature", [])
    if sig and all(any(s.lower() in c.lower() for c in cell_texts) for s in sig):
        score += 5

    return score


def select_descriptor(descriptors: list[Descriptor], *, filename: str,
                      cell_texts: list[str]) -> tuple[Descriptor | None, list[tuple[str, int]]]:
    """Хамгийн өндөр оноотой, min_score давсан descriptor. Хоёрдугаарт бүх оноо (лог)."""
    scored = sorted(
        ((d, fingerprint_score(d, filename=filename, cell_texts=cell_texts))
         for d in descriptors),
        key=lambda t: t[1], reverse=True,
    )
    log = [(d.id, s) for d, s in scored]
    if scored:
        best, best_score = scored[0]
        if best_score >= best.fingerprint.get("min_score", 6):
            return best, log
    return None, log

"""Load the workflow JSON schemas and the disposition catalogue."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    aliases: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    default: str | None = None
    reference: bool = False


@dataclass(frozen=True)
class Schema:
    workflow_id: str
    fields: list[FieldSpec]

    @property
    def reference_field(self) -> str | None:
        for spec in self.fields:
            if spec.reference:
                return spec.name
        return None


@dataclass(frozen=True)
class Disposition:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


def default_schemas_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assignment-a-data" / "schemas"


def load_schemas(directory: Path | None = None) -> dict[str, Schema]:
    directory = directory or default_schemas_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"Schema directory not found: {directory}")
    schemas: dict[str, Schema] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        fields = [
            FieldSpec(
                name=item["name"],
                type=item["type"],
                required=bool(item.get("required", False)),
                pii=bool(item.get("pii", False)),
                aliases=list(item.get("aliases", [])),
                values=list(item.get("values", [])),
                default=item.get("default"),
                reference=bool(item.get("reference", False)),
            )
            for item in data["fields"]
        ]
        schemas[data["workflow_id"]] = Schema(workflow_id=data["workflow_id"], fields=fields)
    if not schemas:
        raise FileNotFoundError(f"No workflow schemas in {directory}")
    return schemas


def load_dispositions(directory: Path | None = None) -> dict[tuple[str, str], Disposition]:
    directory = directory or default_schemas_dir()
    path = directory / "dispositions.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Disposition catalogue not found: {path}")
    catalogue: dict[tuple[str, str], Disposition] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item = Disposition(
                workflow_id=row["workflow_id"].strip(),
                code=row["code"].strip(),
                label=row["label"].strip(),
                is_terminal=row["is_terminal"].strip().lower() == "true",
                is_success=row["is_success"].strip().lower() == "true",
            )
            catalogue[(item.workflow_id, item.code)] = item
    return catalogue

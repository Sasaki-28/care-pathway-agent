"""Pathway definitions, loaded from pathways.json.

This is what makes the engine generic: the state machine, tools, webhooks and screens
are shared. A new disease is a new entry in pathways.json, with no code changes. The
admission dropdown, the server's validation, the triage suggestion and the agents'
prompts all read from here.

The example_* fields only pre-fill the demo screens.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("pathways.json")


@dataclass(frozen=True)
class Pathway:
    id: str
    name: str                      # full name shown in the case file
    short_name: str                # short label for lists
    tests: list[str]
    requires_imaging: bool
    imaging_focus: str
    lab_focus: str
    follow_up_plan: str
    escalation_rules: list[str] = field(default_factory=list)
    completion_rule: str = ""
    triage_keywords: list[str] = field(default_factory=list)   # offline triage fallback
    # demo pre-fill only
    example_symptoms: str = ""
    example_doctor_notes: str = ""
    example_check_ins: dict[str, str] = field(default_factory=dict)   # on_track / warning / finished

    def public(self) -> dict:
        return asdict(self)


REQUIRED = ("id", "name", "short_name", "tests", "requires_imaging", "lab_focus",
            "follow_up_plan", "escalation_rules", "completion_rule")


def load_pathways(path: Path = CONFIG_FILE) -> dict[str, Pathway]:
    """Read and check the config file. A broken entry stops start-up with a clear message."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    pathways: dict[str, Pathway] = {}
    allowed = set(Pathway.__dataclass_fields__)
    for entry in raw["pathways"]:
        missing = [k for k in REQUIRED if not entry.get(k) and entry.get(k) is not False]
        if missing:
            raise ValueError(f"Pathway {entry.get('id', '?')!r} in {path.name} is missing: {', '.join(missing)}")
        if entry["requires_imaging"] and not entry.get("imaging_focus"):
            raise ValueError(f"Pathway {entry['id']!r} needs imaging_focus because requires_imaging is true")
        unknown = set(entry) - allowed
        if unknown:
            raise ValueError(f"Pathway {entry['id']!r} has unknown fields: {', '.join(sorted(unknown))}")
        entry = {"imaging_focus": "", **entry}
        if entry["id"] in pathways:
            raise ValueError(f"Duplicate pathway id {entry['id']!r} in {path.name}")
        pathways[entry["id"]] = Pathway(**entry)
    return pathways


PATHWAYS: dict[str, Pathway] = load_pathways()


def get_pathway(pathway_id: str) -> Pathway | None:
    return PATHWAYS.get((pathway_id or "").lower())


def pathway_ids() -> str:
    """e.g. "tb, pneumonia or diabetes", for prompts and error messages."""
    ids = list(PATHWAYS)
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " or " + ids[-1]

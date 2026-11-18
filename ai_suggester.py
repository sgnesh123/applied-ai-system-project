"""AI task suggester for PawPal+ (retrieval-augmented generation).

Flow (see diagrams/system_diagram.mmd):
    retrieve_notes -> build_prompt -> LLM client -> parse_and_validate
                                                 -> fallback_tasks if nothing valid
The returned suggestions are reviewed by the owner in the UI, and accepted
ones become real Task objects that the Scheduler plans.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pawpal_system import CATEGORIES, PRIORITY_RANK, Owner, Pet, Task, format_time, to_minutes

logger = logging.getLogger("pawpal_ai")

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
LOG_FILE = Path(__file__).parent / "pawpal_ai.log"
MAX_SUGGESTIONS = 8
MAX_DURATION = 240
FALLBACK_SOURCE = "fallback (default tasks)"
MAX_NAME_LENGTH = 30  # pet names go into the prompt, so keep them short


# ---------------------------------------------------------------- data types


@dataclass
class Suggestion:
    """One suggested task plus where it came from and why."""

    task: Task
    source: str
    reason: str = ""


@dataclass
class SuggestionResult:
    """Everything the UI needs to show the owner."""

    suggestions: list[Suggestion]
    notes: dict[str, str]  # filename -> note text that was retrieved
    used_fallback: bool = False
    rejected: list[str] = field(default_factory=list)  # why individual items were dropped


# ------------------------------------------------------------------ logging


def setup_logging(log_file: Path | str = LOG_FILE) -> None:
    """Send pawpal_ai log records to a file. Safe to call more than once."""
    log_file = Path(log_file)
    if any(getattr(h, "baseFilename", None) == str(log_file.resolve()) for h in logger.handlers):
        return
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------- retriever


def life_stage(species: str, age: int) -> str | None:
    """Return the life stage used to pick notes, or None for species without notes."""
    species = species.lower()
    if species == "dog":
        return "puppy" if age < 1 else "senior" if age >= 8 else "adult"
    if species == "cat":
        return "kitten" if age < 1 else "senior" if age >= 11 else "adult"
    return None


def retrieve_notes(pet: Pet, knowledge_dir: Path | str = KNOWLEDGE_DIR) -> dict[str, str]:
    """Return {filename: text} for the notes matching the pet's species and life stage.

    general.md is always included. Missing files are logged and skipped.
    """
    knowledge_dir = Path(knowledge_dir)
    wanted = []
    stage = life_stage(pet.species, pet.age)
    if stage:
        wanted.append(f"{pet.species.lower()}_{stage}.md")
    wanted.append("general.md")

    notes = {}
    for name in wanted:
        path = knowledge_dir / name
        if path.is_file():
            notes[name] = path.read_text(encoding="utf-8").strip()
        else:
            logger.warning("Knowledge note missing: %s", path)
    logger.info("Retrieved notes for %s (%s, age %s): %s", pet.name, pet.species, pet.age, list(notes))
    return notes


# ----------------------------------------------------------- prompt builder

SYSTEM_PROMPT = f"""You are a pet care planning assistant.
Suggest daily or weekly care tasks for ONE pet, using ONLY the care notes provided.
Every task must be supported by one of the notes, and its "source" must be that note's filename.

Return ONLY valid JSON: a list of at most {MAX_SUGGESTIONS} objects, no markdown, no extra text.
Each object has exactly these keys:
  "description": short task name (string)
  "duration_minutes": integer from 1 to {MAX_DURATION}
  "priority": one of {sorted(PRIORITY_RANK)}
  "category": one of {CATEGORIES}
  "frequency": "daily" or "weekly"
  "preferred_start": "HH:MM" if the task should happen at a fixed time, otherwise null
  "source": filename of the note that supports this task
  "reason": one short sentence citing the note"""


def build_prompt(pet: Pet, owner: Owner, notes: dict[str, str]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) combining the pet info with the retrieved notes."""
    # Pet names are free text that goes into the prompt: flatten newlines and cap the length.
    name = " ".join(pet.name.split())[:MAX_NAME_LENGTH]
    existing = ", ".join(task.description for task in pet.tasks) or "none"
    notes_text = "\n\n".join(f"--- {name} ---\n{text}" for name, text in notes.items())
    user_prompt = (
        f"Pet: {name}, a {pet.age}-year-old {pet.species}.\n"
        f"Owner's day starts at {format_time(owner.day_start)} and they have "
        f"{owner.available_minutes} minutes for pet care today (shared across all pets).\n"
        f"Tasks this pet already has (do not repeat them): {existing}.\n\n"
        f"Care notes:\n{notes_text}"
    )
    return SYSTEM_PROMPT, user_prompt


# ---------------------------------------------------------------- validator


def _extract_json(raw: str):
    """Parse JSON from an LLM reply, tolerating ```json fences and surrounding text."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])  # may raise; caller handles it
        raise


def _validate_item(item, allowed_sources: set[str]) -> Suggestion:
    """Turn one JSON object into a Suggestion, or raise ValueError explaining why not."""
    if not isinstance(item, dict):
        raise ValueError("not a JSON object")

    description = str(item.get("description", "")).strip()
    if not description:
        raise ValueError("missing description")
    if len(description) > 60:
        raise ValueError(f"description too long: {description[:30]!r}...")

    duration = item.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or int(duration) != duration:
        raise ValueError(f"{description}: duration must be a whole number")
    duration = int(duration)
    if not 1 <= duration <= MAX_DURATION:
        raise ValueError(f"{description}: duration {duration} outside 1-{MAX_DURATION}")

    priority = str(item.get("priority", "")).lower()
    if priority not in PRIORITY_RANK:
        raise ValueError(f"{description}: invalid priority {priority!r}")

    category = str(item.get("category", "")).lower()
    if category not in CATEGORIES:
        raise ValueError(f"{description}: invalid category {category!r}")

    frequency = str(item.get("frequency", "daily")).lower()
    if frequency not in ("daily", "weekly"):
        raise ValueError(f"{description}: invalid frequency {frequency!r}")

    preferred_start = None
    raw_start = item.get("preferred_start")
    if raw_start not in (None, "", "null"):
        if not re.fullmatch(r"\d{1,2}:\d{2}", str(raw_start).strip()):
            raise ValueError(f"{description}: invalid time {raw_start!r}")
        preferred_start = to_minutes(str(raw_start))
        if not 0 <= preferred_start < 24 * 60 or int(str(raw_start).split(":")[1]) >= 60:
            raise ValueError(f"{description}: invalid time {raw_start!r}")

    source = str(item.get("source", "")).strip()
    if source not in allowed_sources:
        raise ValueError(f"{description}: source {source!r} is not one of the retrieved notes")

    task = Task(description, duration, priority, category, frequency, preferred_start)
    return Suggestion(task=task, source=source, reason=str(item.get("reason", "")).strip())


def parse_and_validate(raw: str, allowed_sources: set[str], pet: Pet) -> tuple[list[Suggestion], list[str]]:
    """Return (valid suggestions, rejection messages) for an LLM reply.

    Drops items with bad fields, items not grounded in a retrieved note, and
    duplicates of the pet's existing tasks. Keeps at most MAX_SUGGESTIONS.
    """
    if not raw.strip():
        return [], ["LLM returned an empty response"]
    try:
        data = _extract_json(raw)
    except json.JSONDecodeError as exc:
        return [], [f"LLM reply was not valid JSON ({exc.msg})"]
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        data = data["tasks"]
    if not isinstance(data, list):
        return [], ["LLM reply was not a JSON list of tasks"]

    seen = {task.description.lower() for task in pet.tasks}
    valid, rejected = [], []
    for item in data:
        try:
            suggestion = _validate_item(item, allowed_sources)
        except ValueError as exc:
            rejected.append(str(exc))
            continue
        key = suggestion.task.description.lower()
        if key in seen:
            rejected.append(f"{suggestion.task.description}: duplicate of an existing task")
            continue
        if len(valid) >= MAX_SUGGESTIONS:
            rejected.append(f"{suggestion.task.description}: over the {MAX_SUGGESTIONS}-task limit")
            continue
        seen.add(key)
        valid.append(suggestion)
    return valid, rejected


# ----------------------------------------------------------------- fallback

_FALLBACK = {
    "dog": [
        ("Breakfast", 10, "high", "feeding"),
        ("Dinner", 10, "high", "feeding"),
        ("Walk", 30, "high", "walk"),
        ("Play time", 15, "medium", "enrichment"),
        ("Refresh water", 2, "medium", "general"),
    ],
    "cat": [
        ("Breakfast", 5, "high", "feeding"),
        ("Dinner", 5, "high", "feeding"),
        ("Scoop litter box", 5, "high", "cleaning"),
        ("Interactive play", 15, "medium", "enrichment"),
        ("Refresh water", 2, "medium", "general"),
    ],
    "other": [
        ("Feeding", 10, "high", "feeding"),
        ("Refresh water", 2, "high", "general"),
        ("Daily health check", 5, "medium", "general"),
    ],
}


def fallback_tasks(pet: Pet) -> list[Suggestion]:
    """Return safe default suggestions for the pet's species, skipping tasks it already has."""
    existing = {task.description.lower() for task in pet.tasks}
    defaults = _FALLBACK.get(pet.species.lower(), _FALLBACK["other"])
    return [
        Suggestion(Task(desc, minutes, priority, category), FALLBACK_SOURCE, "Default task used because no valid AI suggestions were available.")
        for desc, minutes, priority, category in defaults
        if desc.lower() not in existing
    ]


# ------------------------------------------------------------- entry point


def suggest_tasks(pet: Pet, owner: Owner, client, knowledge_dir: Path | str = KNOWLEDGE_DIR) -> SuggestionResult:
    """Retrieve notes, ask the LLM for tasks, validate them, and fall back if needed."""
    notes = retrieve_notes(pet, knowledge_dir)
    system_prompt, user_prompt = build_prompt(pet, owner, notes)

    try:
        raw = client.complete(system_prompt, user_prompt) or ""
    except Exception as exc:  # a client should not raise, but never let it crash the app
        logger.error("LLM client raised: %s", exc)
        raw = ""
    logger.info("LLM raw reply (%d chars): %s", len(raw), raw[:500].replace("\n", " "))

    suggestions, rejected = parse_and_validate(raw, set(notes), pet)
    for reason in rejected:
        logger.warning("Rejected: %s", reason)

    if suggestions:
        logger.info("Accepted %d AI suggestions for %s", len(suggestions), pet.name)
        return SuggestionResult(suggestions, notes, used_fallback=False, rejected=rejected)

    fallback = fallback_tasks(pet)
    logger.warning("No valid AI suggestions for %s; using %d fallback tasks", pet.name, len(fallback))
    return SuggestionResult(fallback, notes, used_fallback=True, rejected=rejected)

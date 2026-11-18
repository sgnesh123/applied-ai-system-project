"""Demo: run the AI task suggester for three sample pets and print the results.

    python demo_ai.py            # uses Gemini if GEMINI_API_KEY is set, otherwise offline mode
    python demo_ai.py --offline  # force offline mode (fallback tasks)
    python demo_ai.py --guardrail  # feed a bad AI reply through the validator

Output is plain text so it can be pasted into the README's Sample Interactions.
"""

import json
import sys
from datetime import date

from dotenv import load_dotenv

from ai_suggester import setup_logging, suggest_tasks
from llm_client import MockClient, get_client
from pawpal_system import Owner, Pet, Scheduler, Task, format_time, to_minutes


BAD_REPLY = [
    {"description": "Breakfast", "duration_minutes": 10, "priority": "high", "category": "feeding",
     "frequency": "daily", "preferred_start": "07:30", "source": "dog_puppy.md", "reason": "Puppies need 3 meals a day."},
    {"description": "Puppy kindergarten", "duration_minutes": 90, "priority": "high", "category": "enrichment",
     "frequency": "weekly", "preferred_start": None, "source": "akc.org", "reason": "Classes help socialization."},
    {"description": "Long run", "duration_minutes": 300, "priority": "medium", "category": "walk",
     "frequency": "daily", "preferred_start": None, "source": "dog_puppy.md", "reason": "Exercise."},
    {"description": "Midnight snack", "duration_minutes": 5, "priority": "urgent", "category": "feeding",
     "frequency": "daily", "preferred_start": "24:30", "source": "dog_puppy.md", "reason": "Hungry."},
]


class CannedClient:
    """Returns a fixed, partly invalid reply (in a markdown fence) to show the validator."""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "```json\n" + json.dumps(BAD_REPLY, indent=2) + "\n```"


def guardrail_demo() -> None:
    """Feed a bad reply through suggest_tasks and print what the validator kept and rejected."""
    owner = Owner("Jordan")
    pet = Pet("Mochi", "dog", age=0)
    owner.add_pet(pet)
    print("Client: CannedClient (fixed reply: 1 valid task, 3 invalid)\n")
    result = suggest_tasks(pet, owner, CannedClient())
    print(f"Used fallback: {result.used_fallback}")
    for s in result.suggestions:
        print(f"  KEPT: {s.task.description} ({s.task.duration_minutes} min) [source: {s.source}]")
    for reason in result.rejected:
        print(f"  REJECTED: {reason}")


def main() -> None:
    load_dotenv()
    setup_logging()
    if "--guardrail" in sys.argv:
        guardrail_demo()
        return
    client = MockClient() if "--offline" in sys.argv else get_client()
    print(f"Client: {type(client).__name__}\n")

    owner = Owner("Jordan", available_minutes=120, day_start=to_minutes("07:30"))
    mochi = Pet("Mochi", "dog", age=0)
    luna = Pet("Luna", "cat", age=12)
    luna.add_task(Task("Breakfast", 5, "high", "feeding", preferred_start=to_minutes("07:30")))
    kiwi = Pet("Kiwi", "other", age=2)
    for pet in (mochi, luna, kiwi):
        owner.add_pet(pet)

    for pet in owner.pets:
        result = suggest_tasks(pet, owner, client)
        existing = ", ".join(t.description for t in pet.tasks) or "none"
        print(f"INPUT: {pet.name}, {pet.age}-year-old {pet.species} | existing tasks: {existing}")
        print(f"Retrieved notes: {', '.join(result.notes) or 'none'}")
        print(f"Used fallback: {result.used_fallback}")
        for s in result.suggestions:
            pinned = f" @ {format_time(s.task.preferred_start)}" if s.task.preferred_start is not None else ""
            print(
                f"  - {s.task.description} ({s.task.duration_minutes} min, {s.task.priority}, "
                f"{s.task.category}, {s.task.frequency}{pinned}) [source: {s.source}]"
            )
            if s.reason:
                print(f"      why: {s.reason}")
        for reason in result.rejected:
            print(f"  REJECTED: {reason}")
        print()
        for s in result.suggestions:  # accept everything for the demo
            pet.add_task(s.task)

    scheduler = Scheduler(owner)
    scheduler.generate_plan(date.today())
    print(scheduler.explain_plan())


if __name__ == "__main__":
    main()

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from app.database import init_db
from app.graph.profile_graph import profile_graph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CareerTrace profile-onboarding workflow."
    )
    parser.add_argument("resume_path", type=Path, help="Path to a local resume PDF.")
    return parser.parse_args()


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__") or ()
    if not interrupts:
        return None
    item = interrupts[0]
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else None


def _prompt_for_missing_fields(fields: list[str]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for field in fields:
        if field == "graduation_year":
            updates[field] = int(input("Graduation year: ").strip())
        elif field == "skills":
            updates[field] = [
                item.strip()
                for item in input("Skills (comma-separated): ").split(",")
                if item.strip()
            ]
        elif field == "experience":
            description = input("Experience description: ").strip()
            updates[field] = [
                {"organization": "", "role": "", "description": description}
            ]
        else:
            updates[field] = input(f"{field.replace('_', ' ').title()}: ").strip()
    return updates


def _invoke(command: Any, thread_id: str) -> dict[str, Any]:
    return profile_graph.invoke(
        command,
        config={"configurable": {"thread_id": thread_id}},
    )


def main() -> None:
    args = parse_args()
    init_db()
    thread_id = str(uuid4())
    final_state = _invoke(
        {
            "resume_path": str(args.resume_path.expanduser()),
            "confirmed": False,
        },
        thread_id,
    )

    while pending := _interrupt_value(final_state):
        if pending.get("type") == "missing_profile_fields":
            print("\nSome required profile information is missing.")
            updates = _prompt_for_missing_fields(pending["missing_fields"])
            final_state = _invoke(Command(resume=updates), thread_id)
        elif pending.get("type") == "confirm_profile":
            print("\nExtracted profile:")
            print(json.dumps(pending["profile"], indent=2, ensure_ascii=False))
            response = input("\nIs this information correct? (yes/no) ").strip().lower()
            final_state = _invoke(
                Command(
                    resume={
                        "confirmed": response in {"yes", "y"},
                        "profile": pending["profile"],
                    }
                ),
                thread_id,
            )
        else:
            raise RuntimeError(f"Unknown workflow interrupt: {pending}")

    if not final_state.get("confirmed"):
        print("\nProfile was not confirmed. No data was saved.")
        return

    print("\nCareer analysis:")
    print(json.dumps(final_state["career_profile"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

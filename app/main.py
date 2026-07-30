import argparse
import json
from pathlib import Path

from app.graph.profile_graph import profile_graph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CareerTrace profile-onboarding workflow."
    )
    parser.add_argument("resume_path", type=Path, help="Path to a local resume PDF.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    final_state = profile_graph.invoke(
        {"resume_path": str(args.resume_path.expanduser())}
    )

    if not final_state.get("confirmed"):
        print("\nProfile was not confirmed. No data was saved.")
        return

    print("\nCareer profile:")
    print(json.dumps(final_state["career_profile"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

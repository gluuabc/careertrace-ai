import json

from app.state.schema import ProfileState


def confirm_profile(state: ProfileState) -> dict[str, bool]:
    """Deterministically collect the user's terminal confirmation."""

    print("\nExtracted profile:")
    print(json.dumps(state["extracted_profile"], indent=2, ensure_ascii=False))

    while True:
        response = input("\nIs this information correct? (yes/no) ").strip().lower()
        if response in {"yes", "y"}:
            return {"confirmed": True}
        if response in {"no", "n"}:
            return {"confirmed": False}
        print("Please enter 'yes' or 'no'.")

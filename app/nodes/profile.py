import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.database.repository import profile_repository
from app.llm.model import get_llm
from app.services.token_accounting import ModelCallObserver
from app.state.schema import CareerProfile, ProfileState


def _as_dict(value: BaseModel | dict) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected structured career data, received {type(value).__name__}")


def generate_profile(state: ProfileState) -> dict[str, dict]:
    """Use the reasoning Bedrock model to generate initial career guidance."""

    prompt_profile = {
        key: value
        for key, value in state["saved_profile"].items()
        if key
        not in {
            "user_id",
            "email",
            "profile_version",
            "profile_version_id",
            "profile_changed",
            "updated_at",
            "source_documents",
        }
    }
    structured_llm = get_llm("reasoning").with_structured_output(CareerProfile)
    messages = [
            SystemMessage(
                content=(
                    "You are a practical career assistant. Base every recommendation "
                    "on the confirmed profile. Keep suggestions specific, realistic, "
                    "and suitable for an initial career plan."
                )
            ),
            HumanMessage(
                content=(
                    "Generate the candidate's strengths, possible roles, and "
                    "recommended next skills from this confirmed profile:\n\n"
                    f"{json.dumps(prompt_profile, indent=2)}"
                )
            ),
        ]
    result = ModelCallObserver(profile_repository).invoke(
        structured_llm,
        messages,
        user_id=state["user_id"],
        conversation_id=None,
        run_id=None,
        stage="career_profile_generation",
        model_type="reasoning",
        model_id=os.getenv("BEDROCK_MODEL_REASONING", ""),
    )

    return {"career_profile": _as_dict(result)}

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.model import get_llm
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
        if key not in {"profile_version", "updated_at"}
    }
    structured_llm = get_llm("reasoning").with_structured_output(CareerProfile)
    result = structured_llm.invoke(
        [
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
    )

    return {"career_profile": _as_dict(result)}

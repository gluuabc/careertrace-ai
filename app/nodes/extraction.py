from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.model import get_llm
from app.state.schema import ProfileFacts, ProfileState


def _as_dict(value: BaseModel | dict) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected structured profile data, received {type(value).__name__}")


def extract_profile(state: ProfileState) -> dict[str, dict]:
    """Use the low-cost Bedrock model for structured resume extraction."""

    structured_llm = get_llm("cheap").with_structured_output(ProfileFacts)
    result = structured_llm.invoke(
        [
            SystemMessage(
                content=(
                    "Extract only facts explicitly supported by the resume. "
                    "Do not guess missing details. Use empty lists or null values "
                    "when the resume does not provide a field."
                )
            ),
            HumanMessage(
                content=(
                    "Extract the candidate's education, school, major, "
                    "graduation_year, skills, projects, and experience from this "
                    f"resume:\n\n{state['resume_text']}"
                )
            ),
        ]
    )

    return {"extracted_profile": _as_dict(result)}

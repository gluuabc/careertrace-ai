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


def merge_candidate_profile(
    existing: dict | None, extracted: dict
) -> dict:
    """Merge new evidence into confirmed facts before human review."""

    if not existing:
        return ProfileFacts.model_validate(extracted).model_dump()
    merged = ProfileFacts.model_validate(existing).model_dump()
    candidate = ProfileFacts.model_validate(extracted).model_dump()
    list_fields = (
        "education",
        "skills",
        "courses",
        "achievements",
        "certifications",
        "projects",
        "experience",
        "target_roles",
        "preferred_locations",
        "employment_types",
    )
    for field in list_fields:
        values = [*merged.get(field, []), *candidate.get(field, [])]
        unique: list = []
        seen: set[str] = set()
        for value in values:
            key = str(value).strip().casefold()
            if key and key not in seen:
                seen.add(key)
                unique.append(value)
        merged[field] = unique
    for field, value in candidate.items():
        if field not in list_fields and value not in (None, "", []):
            merged[field] = value
    return ProfileFacts.model_validate(merged).model_dump()


def extract_profile(state: ProfileState) -> dict[str, dict]:
    """Use the low-cost Bedrock model for structured resume extraction."""

    structured_llm = get_llm("cheap").with_structured_output(ProfileFacts)
    result = structured_llm.invoke(
        [
            SystemMessage(
                content=(
                    "Extract only facts explicitly supported by the supplied career "
                    "documents. Do not "
                    "guess missing details. Separate each project into a title and "
                    "description, and each experience into organization, role, and "
                    "description. Use empty strings, empty lists, or null values when "
                    "the documents do not provide a field. Respect each document's "
                    "type label. Career preferences should remain empty unless they "
                    "are explicitly stated."
                )
            ),
            HumanMessage(
                content=(
                    "Extract the candidate's name, email, education, school, major, "
                    "graduation_year, skills, courses, achievements, certifications, "
                    "projects, and experience from these "
                    f"documents:\n\n{state['resume_text']}"
                )
            ),
        ]
    )

    return {
        "extracted_profile": merge_candidate_profile(
            state.get("existing_profile"), _as_dict(result)
        )
    }

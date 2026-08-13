from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.skill_registry import skill_registry
from app.state.agent_schema import ToolExecutionResult


@tool
def read_skill(name: str) -> dict:
    """Load the full registered Skill instructions after a workflow is selected. Use only an exact Skill name from the catalog; this has no user-data side effect."""
    try:
        return ToolExecutionResult(ok=True, data={"name": name, "content": skill_registry.read_skill(name)}).model_dump()
    except ValueError as error:
        return ToolExecutionResult(ok=False, error_type="SkillError", error_message=str(error)).model_dump()


@tool
def read_skill_file(
    name: str,
    relative_path: str,
    offset: int = 0,
    limit: int = 4000,
    active_skill: Annotated[str, InjectedState("active_skill")] = "",
) -> dict:
    """Load one approved supporting text file from a registered Skill when its detailed rules are needed. Absolute paths, traversal, symlinks, and unsupported files are rejected."""
    try:
        if not active_skill or name != active_skill:
            raise ValueError("Skill files may be read only for the active workflow Skill.")
        if offset < 0 or limit < 1 or limit > 8000:
            raise ValueError("Skill offset must be non-negative and limit must be 1–8000.")
        content = skill_registry.read_skill_file(name, relative_path)
        page = content[offset : offset + limit]
        next_offset = offset + len(page)
        return ToolExecutionResult(
            ok=True,
            data={
                "name": name,
                "relative_path": relative_path,
                "content": page,
                "offset": offset,
                "returned_count": len(page),
                "total_count": len(content),
                "has_more": next_offset < len(content),
                "next_offset": next_offset if next_offset < len(content) else None,
                "truncated": next_offset < len(content),
            },
        ).model_dump()
    except ValueError as error:
        return ToolExecutionResult(ok=False, error_type="SkillFileError", error_message=str(error)).model_dump()

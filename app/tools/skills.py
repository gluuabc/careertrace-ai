from langchain_core.tools import tool

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
def read_skill_file(name: str, relative_path: str) -> dict:
    """Load one approved supporting text file from a registered Skill when its detailed rules are needed. Absolute paths, traversal, symlinks, and unsupported files are rejected."""
    try:
        return ToolExecutionResult(ok=True, data={"name": name, "relative_path": relative_path, "content": skill_registry.read_skill_file(name, relative_path)}).model_dump()
    except ValueError as error:
        return ToolExecutionResult(ok=False, error_type="SkillFileError", error_message=str(error)).model_dump()

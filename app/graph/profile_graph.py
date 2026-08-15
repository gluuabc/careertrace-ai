from langgraph.graph import END, START, StateGraph

from app.graph.checkpoint import get_default_checkpointer
from app.nodes.confirmation import confirm_profile
from app.nodes.documents import store_document
from app.nodes.extraction import extract_profile
from app.nodes.memory import save_profile
from app.nodes.resume import extract_resume
from app.nodes.validation import collect_missing_information, validate_profile
from app.state.schema import ProfileState


def _route_after_validation(state: ProfileState) -> str:
    return "missing" if state.get("missing_fields") else "complete"


def _route_after_confirmation(state: ProfileState) -> str:
    if state.get("confirmed"):
        return "confirmed"
    if state.get("missing_fields") or state.get("validation_errors"):
        return "invalid"
    return "rejected"


def build_profile_graph(checkpointer=None):
    """Build the controlled profile-onboarding LangGraph workflow."""

    workflow = StateGraph(ProfileState)

    # Deterministic private storage and document-processing nodes.
    workflow.add_node("store_document", store_document)
    workflow.add_node("extract_resume", extract_resume)
    # LLM reasoning node using the low-cost model.
    workflow.add_node("extract_profile", extract_profile)
    # Deterministic validation and human-in-the-loop collection nodes.
    workflow.add_node("validate_profile", validate_profile)
    workflow.add_node("collect_missing_information", collect_missing_information)
    workflow.add_node("confirm_profile", confirm_profile)
    # Deterministic SQL persistence nodes.
    workflow.add_node("save_profile", save_profile)

    workflow.add_edge(START, "store_document")
    workflow.add_edge("store_document", "extract_resume")
    workflow.add_edge("extract_resume", "extract_profile")
    workflow.add_edge("extract_profile", "validate_profile")
    workflow.add_conditional_edges(
        "validate_profile",
        _route_after_validation,
        {
            "missing": "collect_missing_information",
            "complete": "confirm_profile",
        },
    )
    workflow.add_edge("collect_missing_information", "validate_profile")
    workflow.add_conditional_edges(
        "confirm_profile",
        _route_after_confirmation,
        {
            "confirmed": "save_profile",
            "invalid": "confirm_profile",
            "rejected": END,
        },
    )
    workflow.add_edge("save_profile", END)

    return workflow.compile(
        checkpointer=(
            checkpointer if checkpointer is not None else get_default_checkpointer()
        )
    )


profile_graph = build_profile_graph()

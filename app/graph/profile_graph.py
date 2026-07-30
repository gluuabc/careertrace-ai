from langgraph.graph import END, START, StateGraph

from app.nodes.confirmation import confirm_profile
from app.nodes.extraction import extract_profile
from app.nodes.memory import save_profile
from app.nodes.profile import generate_profile
from app.nodes.resume import extract_resume
from app.state.schema import ProfileState


def _route_after_confirmation(state: ProfileState) -> str:
    """Choose the deterministic branch after the human approval gate."""

    return "confirmed" if state.get("confirmed") else "rejected"


def build_profile_graph():
    """Build the controlled profile-onboarding LangGraph workflow."""

    workflow = StateGraph(ProfileState)

    # Deterministic document-processing node.
    workflow.add_node("extract_resume", extract_resume)
    # LLM reasoning node using the low-cost model.
    workflow.add_node("extract_profile", extract_profile)
    # Deterministic human approval gate.
    workflow.add_node("confirm_profile", confirm_profile)
    # Deterministic persistence node.
    workflow.add_node("save_profile", save_profile)
    # LLM reasoning node using the stronger model.
    workflow.add_node("generate_profile", generate_profile)

    workflow.add_edge(START, "extract_resume")
    workflow.add_edge("extract_resume", "extract_profile")
    workflow.add_edge("extract_profile", "confirm_profile")
    workflow.add_conditional_edges(
        "confirm_profile",
        _route_after_confirmation,
        {
            "confirmed": "save_profile",
            "rejected": END,
        },
    )
    workflow.add_edge("save_profile", "generate_profile")
    workflow.add_edge("generate_profile", END)

    return workflow.compile()


profile_graph = build_profile_graph()

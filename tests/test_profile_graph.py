import unittest
from unittest.mock import patch
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import app.graph.profile_graph as graph_module


def _interrupt_value(result):
    interrupts = result.get("__interrupt__") or ()
    return interrupts[0].value if interrupts else None


class ProfileGraphTests(unittest.TestCase):
    def test_invalid_confirmation_routes_back_to_confirmation(self):
        self.assertEqual(
            graph_module._route_after_confirmation(
                {
                    "confirmed": False,
                    "missing_fields": ["major"],
                    "validation_errors": [],
                }
            ),
            "invalid",
        )

    def test_profile_onboarding_does_not_generate_career_analysis(self):
        extracted = {
            "name": "Ada Student",
            "email": None,
            "education": ["B.S. Computer Science"],
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": None,
            "skills": [],
            "projects": [],
            "experience": [],
            "career_goal": None,
            "target_roles": [],
            "preferred_locations": [],
            "employment_types": [],
            "work_authorization": None,
            "remote_preference": None,
        }

        fake_nodes = {
            "store_document": lambda _state: {
                "document_id": "document-1",
                "s3_key": "user-1/document-1/resume.pdf",
            },
            "extract_resume": lambda _state: {"resume_text": "resume text"},
            "extract_profile": lambda _state: {"extracted_profile": extracted},
            "save_profile": lambda state: {
                "user_id": "user-1",
                "saved_profile": {
                    **state["extracted_profile"],
                    "profile_version": 1,
                    "profile_changed": True,
                },
            },
        }

        with (
            patch.object(
                graph_module, "store_document", fake_nodes["store_document"]
            ),
            patch.object(graph_module, "extract_resume", fake_nodes["extract_resume"]),
            patch.object(
                graph_module, "extract_profile", fake_nodes["extract_profile"]
            ),
            patch.object(graph_module, "save_profile", fake_nodes["save_profile"]),
        ):
            graph = graph_module.build_profile_graph(MemorySaver())

        config = {"configurable": {"thread_id": str(uuid4())}}
        result = graph.invoke(
            {"resume_path": "resume.pdf", "user_id": "user-1"}, config=config
        )
        pending = _interrupt_value(result)
        self.assertEqual(pending["type"], "missing_profile_fields")

        result = graph.invoke(
            Command(
                resume={
                    "graduation_year": 2028,
                    "skills": ["Python"],
                    "experience": [
                        {
                            "organization": "Example Lab",
                            "role": "Research Assistant",
                            "description": "",
                        }
                    ],
                }
            ),
            config=config,
        )
        pending = _interrupt_value(result)
        self.assertEqual(pending["type"], "confirm_profile")

        result = graph.invoke(
            Command(
                resume={
                    "confirmed": True,
                    "profile": pending["profile"],
                }
            ),
            config=config,
        )
        self.assertTrue(result["confirmed"])
        self.assertNotIn("analysis_id", result)
        self.assertNotIn("career_profile", result)
        self.assertEqual(result["document_id"], "document-1")


if __name__ == "__main__":
    unittest.main()

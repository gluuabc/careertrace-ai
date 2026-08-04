import os
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.database.database import (
    PROJECT_ROOT,
    create_database_engine,
    create_session_factory,
    init_db,
    resolve_database_url,
)
from app.database.repository import ProfileRepository
from app.graph.checkpoint import create_sqlite_checkpointer


class InterruptState(TypedDict, total=False):
    answer: str


def _question(_state: InterruptState):
    return {"answer": interrupt("continue")}


def _interrupt_graph(checkpointer):
    builder = StateGraph(InterruptState)
    builder.add_node("question", _question)
    builder.add_edge(START, "question")
    builder.add_edge("question", END)
    return builder.compile(checkpointer=checkpointer)


class PersistenceTests(unittest.TestCase):
    def test_relative_database_url_is_repository_rooted(self):
        expected = (PROJECT_ROOT / "data" / "relative-test.db").resolve()
        resolved = resolve_database_url("sqlite:///data/relative-test.db")
        self.assertEqual(Path(resolved.removeprefix("sqlite:///")), expected)

    def test_profile_and_analysis_survive_engine_and_login_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'restart.db'}"
            engine = create_database_engine(url)
            init_db(engine)
            repository = ProfileRepository(create_session_factory(engine))
            user = repository.get_or_create_google_user(
                google_id="restart-google",
                email="restart@example.com",
                name="Restart Student",
            )
            repository.upsert_profile(
                user["user_id"],
                {
                    "school": "Example University",
                    "major": "Computer Science",
                    "graduation_year": 2028,
                    "skills": ["Python"],
                    "experience": [{"role": "Intern"}],
                },
            )
            repository.save_analysis(
                user["user_id"],
                {
                    "strengths": ["Persistence"],
                    "possible_roles": ["Engineer"],
                    "recommended_next_skills": ["SQL"],
                },
            )
            engine.dispose()

            engine = create_database_engine(url)
            repository = ProfileRepository(create_session_factory(engine))
            signed_in_again = repository.get_or_create_google_user(
                google_id="restart-google",
                email="restart@example.com",
                name="Restart Student",
            )
            self.assertEqual(signed_in_again["user_id"], user["user_id"])
            self.assertEqual(
                repository.get_profile(user["user_id"])["major"],
                "Computer Science",
            )
            self.assertEqual(
                repository.get_latest_analysis(user["user_id"])["strengths"],
                ["Persistence"],
            )
            engine.dispose()

    def test_sqlite_checkpoint_resumes_after_connection_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.sqlite"
            first_saver = create_sqlite_checkpointer(path)
            first_graph = _interrupt_graph(first_saver)
            config = {"configurable": {"thread_id": "restart-thread"}}
            result = first_graph.invoke({}, config=config)
            self.assertTrue(result.get("__interrupt__"))
            first_saver.conn.close()

            second_saver = create_sqlite_checkpointer(path)
            second_graph = _interrupt_graph(second_saver)
            result = second_graph.invoke(Command(resume="confirmed"), config=config)
            self.assertEqual(result["answer"], "confirmed")
            second_saver.conn.close()


if __name__ == "__main__":
    unittest.main()

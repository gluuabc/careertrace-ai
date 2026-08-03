import unittest

from app.nodes.confirmation import process_confirmation_response


def _complete_profile() -> dict:
    return {
        "name": "Demo Student",
        "school": "Example University",
        "major": "Computer Science",
        "graduation_year": 2028,
        "skills": ["Python"],
        "projects": [],
        "experience": [
            {
                "organization": "Example Lab",
                "role": "Research Assistant",
                "description": "Built evaluation tools",
            }
        ],
    }


class ProfileConfirmationTests(unittest.TestCase):
    def test_major_initially_missing_then_entered(self):
        current = {**_complete_profile(), "major": None}
        result = process_confirmation_response(
            current,
            {
                "confirmed": True,
                "profile": {**current, "major": "  Computer Science  "},
            },
        )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["extracted_profile"]["major"], "Computer Science")
        self.assertEqual(result["missing_fields"], [])

    def test_whitespace_only_major_remains_invalid(self):
        current = _complete_profile()
        result = process_confirmation_response(
            current,
            {
                "confirmed": True,
                "profile": {**current, "major": "   "},
            },
        )

        self.assertFalse(result["confirmed"])
        self.assertIsNone(result["extracted_profile"]["major"])
        self.assertIn("major", result["missing_fields"])

    def test_all_required_fields_completed_allows_confirmation(self):
        result = process_confirmation_response(
            _complete_profile(),
            {"confirmed": True, "profile": _complete_profile()},
        )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["validation_errors"], [])

    def test_editing_existing_required_field_persists(self):
        current = _complete_profile()
        result = process_confirmation_response(
            current,
            {
                "confirmed": True,
                "profile": {**current, "major": "Data Science"},
            },
        )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["extracted_profile"]["major"], "Data Science")


if __name__ == "__main__":
    unittest.main()

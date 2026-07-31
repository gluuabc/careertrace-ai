import unittest

from app.nodes.validation import find_profile_issues, merge_profile_updates


class ProfileValidationTests(unittest.TestCase):
    def test_detects_required_missing_fields(self):
        missing, errors = find_profile_issues(
            {
                "school": "   ",
                "major": "Computer Science",
                "graduation_year": None,
                "skills": [""],
                "experience": [
                    {"organization": "", "role": "", "description": ""}
                ],
            }
        )

        self.assertEqual(
            missing,
            ["school", "graduation_year", "skills", "experience"],
        )
        self.assertEqual(errors, [])

    def test_merges_and_normalizes_user_updates(self):
        profile = {
            "school": "Example University",
            "major": "Computer Science",
            "graduation_year": None,
            "skills": [],
            "projects": [],
            "experience": [],
        }
        merged = merge_profile_updates(
            profile,
            {
                "graduation_year": 2028,
                "skills": ["Python"],
                "experience": [
                    {
                        "organization": "Example Lab",
                        "role": "Research Assistant",
                        "description": "",
                    }
                ],
            },
        )

        self.assertEqual(merged["graduation_year"], 2028)
        self.assertEqual(merged["skills"], ["Python"])
        self.assertEqual(
            merged["experience"][0]["organization"], "Example Lab"
        )


if __name__ == "__main__":
    unittest.main()

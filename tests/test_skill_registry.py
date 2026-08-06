import tempfile
import unittest
from pathlib import Path

from app.services.skill_registry import SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    def test_scans_metadata_and_loads_layers(self):
        registry = SkillRegistry()
        self.assertIn("job_search", registry.names())
        self.assertIn("job_search:", registry.catalog())
        self.assertIn("hard constraints", registry.read_skill("job_search"))
        self.assertIn("Source policy", registry.read_skill_file("job_search", "source_policy.md"))

    def test_rejects_traversal_and_malformed_frontmatter(self):
        registry = SkillRegistry()
        with self.assertRaisesRegex(ValueError, "inside"):
            registry.read_skill_file("job_search", "../outreach/SKILL.md")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad").mkdir()
            (root / "bad" / "SKILL.md").write_text("# Missing metadata", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frontmatter"):
                SkillRegistry(root)

    def test_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("one", "two"):
                (root / folder).mkdir()
                (root / folder / "SKILL.md").write_text("---\nname: duplicate\ndescription: x\n---\nbody", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                SkillRegistry(root)


if __name__ == "__main__":
    unittest.main()

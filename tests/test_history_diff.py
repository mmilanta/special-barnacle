import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from history_diff import recipe_diff


class RecipeDiffTests(unittest.TestCase):
    def test_all_fields_and_preserved_context(self):
        changes = recipe_diff(
            {"title": "Torta", "ingredients": "Farina\n200 g zucchero\n2 uova"},
            {"title": "Torta", "ingredients": "Farina\n100 g zucchero\n2 uova"},
        )
        self.assertEqual(len(changes), 5)
        self.assertEqual(changes[0]["text"], "Torta")
        self.assertTrue(changes[0]["unchanged"])
        self.assertEqual(changes[2]["label"], "Ingredienti")
        rows = changes[2]["rows"]
        self.assertEqual(
            rows[0], {"before": "Farina", "after": "Farina", "changed": False}
        )
        self.assertEqual(
            rows[1],
            {"before": "200 g zucchero", "after": "100 g zucchero", "changed": True},
        )
        self.assertFalse(rows[2]["changed"])

    def test_insertions_deletions_and_null_notes(self):
        changes = recipe_diff({"notes": None}, {"notes": "Riposa\nin frigo"})
        self.assertEqual(
            changes[4]["rows"][0], {"before": None, "after": "Riposa", "changed": True}
        )
        changes = recipe_diff({"notes": "Riposa"}, {"notes": ""})
        self.assertEqual(
            changes[4]["rows"][0], {"before": "Riposa", "after": None, "changed": True}
        )
        self.assertTrue(
            all(
                field["unchanged"]
                for field in recipe_diff({"notes": None}, {"notes": ""})
            )
        )
        self.assertTrue(all(field["unchanged"] for field in recipe_diff(None, None)))


if __name__ == "__main__":
    unittest.main()

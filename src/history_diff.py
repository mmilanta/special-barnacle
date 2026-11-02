"""Compare recipe fields without exposing JSON or rendering historical HTML."""

from difflib import SequenceMatcher
from itertools import zip_longest

FIELDS = (
    ("title", "Titolo"),
    ("category", "Categoria"),
    ("ingredients", "Ingredienti"),
    ("steps", "Ricetta"),
    ("notes", "Note"),
)


def recipe_diff(before: dict | None, after: dict | None) -> list[dict]:
    changes = []
    for field, label in FIELDS:
        old = (before or {}).get(field) or ""
        new = (after or {}).get(field) or ""
        if old == new:
            changes.append({"label": label, "unchanged": True, "text": old, "rows": []})
            continue
        old_lines, new_lines = old.splitlines(), new.splitlines()
        rows = []
        for operation, a1, a2, b1, b2 in SequenceMatcher(
            None, old_lines, new_lines, autojunk=False
        ).get_opcodes():
            for left, right in zip_longest(old_lines[a1:a2], new_lines[b1:b2]):
                rows.append(
                    {
                        "before": left,
                        "after": right,
                        "changed": operation != "equal",
                    }
                )
        changes.append({"label": label, "unchanged": False, "rows": rows})
    return changes

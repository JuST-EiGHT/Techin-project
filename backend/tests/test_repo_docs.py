"""Документы, которые параллельные сессии сливают построчно (.gitattributes, merge=union): если обе стороны правили одну
строку, после слияния main остаются обе версии — старая и новая. Здесь — поиск таких повторов: шаг в таблице, номер в
нумерованном списке, раздел журнала."""
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STEP = re.compile(r"^\d+\.\d+[a-z]?$")
NUMBERED = re.compile(r"^(\d+)\. \*\*")
HINT = "— после слияния main осталась и старая, и новая версия строки; удалите устаревшую"


def _tables(text: str) -> list[list[list[str]]]:
    tables, cur = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):  # не строка-разделитель
                cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def _repeats(values) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def test_claude_md_status_has_one_row_per_step():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    for table in _tables(text):
        rows = [r[0] for r in table[1:] if r and r[0]]
        assert not _repeats(rows), f"CLAUDE.md, таблица статуса: шаг {', '.join(_repeats(rows))} дважды {HINT}"


def test_roadmap_tables_have_one_row_per_step():
    text = (REPO / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    for table in _tables(text):
        steps = [next((c for c in r[:2] if STEP.match(c)), None) for r in table[1:]]
        steps = [s for s in steps if s]
        assert not _repeats(steps), f"docs/ROADMAP.md: шаг {', '.join(_repeats(steps))} дважды в одной таблице {HINT}"


def test_numbered_lists_have_unique_numbers():
    for path in (REPO / "CLAUDE.md", REPO / "docs" / "ROADMAP.md"):
        nums = [m.group(1) for line in path.read_text(encoding="utf-8").splitlines() if (m := NUMBERED.match(line))]
        assert not _repeats(nums), f"{path.name}: пункт {', '.join(_repeats(nums))} дважды {HINT}"


def test_done_md_sections_unique():
    text = (REPO / "docs" / "DONE.md").read_text(encoding="utf-8")
    heads = [line for line in text.splitlines() if line.startswith("## ")]
    assert not _repeats(heads), f"docs/DONE.md: раздел {'; '.join(_repeats(heads))} дважды {HINT}"

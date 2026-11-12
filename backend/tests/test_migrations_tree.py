"""Миграции из параллельных сессий не конфликтуют (CLAUDE.md, «Параллельные сессии и слияние main»): новая миграция
называется по дате и теме, а не следующим номером, — две сессии не займут одно имя; две ветки миграций после слияния
main сводит миграция-слияние, своя миграция не переименовывается."""
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
LAST_NUMBERED = 49  # 0001 … 0049 — по номерам; дальше — по дате
NUMBERED = re.compile(r"^(\d{4})_[a-z0-9_]+$")
DATED = re.compile(r"^20\d{6}_[a-z0-9_]+$")


def _scripts() -> ScriptDirectory:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, (
        f"Две ветки миграций: {', '.join(sorted(heads))}. Так бывает после слияния main, когда другая сессия тоже "
        "добавила миграцию. Ничего не переименовывайте — добавьте миграцию-слияние: "
        'cd backend && alembic merge heads -m "слияние веток" --rev-id ГГГГММДД_merge_тема'
    )


def test_revision_names():
    for rev in _scripts().walk_revisions():
        name = Path(rev.path).stem
        assert rev.revision == name, f"{name}.py: revision = {rev.revision!r} — должно совпадать с именем файла"
        assert len(name) <= 32, f"{name}: длиннее 32 символов — не поместится в таблицу alembic_version"
        numbered = NUMBERED.match(name)
        assert (numbered and int(numbered.group(1)) <= LAST_NUMBERED) or DATED.match(name), (
            f"{name}.py: новая миграция называется по дате и теме — ГГГГММДД_тема (например 20261112_dashboards), "
            f"а не следующим номером: номер могла занять другая сессия. Номера закончились на {LAST_NUMBERED:04d}."
        )

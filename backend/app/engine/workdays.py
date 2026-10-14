"""Рабочий календарь РФ (данные — app/data/work_calendar.json): выходные, праздники, переносы.

Нужен выплатам Ozon (шаг 3.6) и налоговым срокам (шаг 3.9): срок в выходной или праздник переносится
на следующий рабочий день. Для года без записи о переносах — только выходные и праздники (оценка).
"""
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

CALENDAR_FILE = Path(__file__).resolve().parents[1] / "data" / "work_calendar.json"


@lru_cache(maxsize=1)
def _calendar() -> dict:
    return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))


def is_workday(day: date) -> bool:
    cal = _calendar()
    year = cal["years"].get(str(day.year), {})
    iso = day.isoformat()
    if iso in year.get("work", []):
        return True
    if iso in year.get("off", []) or day.strftime("%m-%d") in cal["fixed_holidays"]:
        return False
    return day.weekday() < 5


def next_workday(day: date) -> date:
    """Сам день, если рабочий, иначе ближайший следующий рабочий."""
    while not is_workday(day):
        day += timedelta(days=1)
    return day


def known_year(year: int) -> bool:
    """Переносы этого года есть в календаре (иначе даты — оценка без переносов)."""
    return str(year) in _calendar()["years"]

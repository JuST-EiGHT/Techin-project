"""Описание шаблона: какие колонки, как называются, что в них писать.

Одно описание служит и для выгрузки шаблона, и для разбора загруженного файла, поэтому файл,
скачанный из сервиса, всегда читается обратно (правило 14). Колонки при загрузке ищутся по
заголовкам (правило 4): заголовок шаблона или любой из вариантов названия.
"""

from dataclasses import dataclass
from typing import Literal

from app.integrations.xlsx.common import norm

Kind = Literal["text", "money", "rate", "date"]


@dataclass(frozen=True)
class Column:
    key: str
    title: str  # заголовок в шаблоне
    kind: Kind = "text"
    aliases: tuple[str, ...] = ()  # другие названия при загрузке — часть текста заголовка, по приоритету
    exclude: tuple[str, ...] = ()  # заголовок с этими словами — не эта колонка («с ндс» у цены без НДС)
    reference: bool = False  # справочная: в шаблоне есть, при загрузке не читается
    in_template: bool = True  # False — только принимается при загрузке (альтернативная колонка)
    width: int = 16
    hint: str = ""  # что писать — для листа «Инструкция»
    choices: tuple[str, ...] = ()  # выпадающий список в Excel (другие значения тоже можно)

    def names(self) -> tuple[str, ...]:
        return (norm(self.title), *self.aliases)

    def matches(self, header: str) -> int | None:
        """Приоритет совпадения с нормализованным заголовком (меньше — точнее) или None."""
        if not header or any(word in header for word in self.exclude):
            return None
        for priority, name in enumerate(self.names()):
            if name and name in header:
                return priority
        return None


@dataclass(frozen=True)
class TemplateSpec:
    sheet: str  # название листа с таблицей
    columns: tuple[Column, ...]
    required: tuple[tuple[str, ...], ...]  # группы ключей: из каждой нужна хотя бы одна колонка
    missing_columns: str  # текст ошибки, если таблица не нашлась
    instructions: tuple[str, ...] = ()  # абзацы листа «Инструкция» перед описанием колонок
    notes: tuple[str, ...] = ()  # абзацы после описания колонок (частые ошибки)

    def column(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)

    @property
    def template_columns(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.in_template)

    @property
    def read_columns(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if not c.reference)


ERROR_COLUMN = Column("_error", "Ошибка", width=48, reference=True)

"""Шаблоны для загрузки списков: скачал → заполнил → загрузил (правило 14, шаг 2.4).

Описание колонок (`TemplateSpec`) — одно на выгрузку и на разбор, поэтому файл, скачанный
из сервиса, всегда принимается обратно. Пользуются: себестоимость (2.4); дальше — плановые
платежи (3.10), массовое изменение цен (6.3).
"""

from app.services.templates.build import INSTRUCTION_SHEET, MEDIA_TYPES, build_csv, build_file, build_xlsx
from app.services.templates.read import Table, TableRow, TemplateError, read_table
from app.services.templates.spec import ERROR_COLUMN, Column, TemplateSpec
from app.services.templates.values import (
    FORMULA,
    RATE_CHOICES,
    CellError,
    echo,
    is_empty,
    parse_day,
    parse_key,
    parse_money,
    parse_rate,
    rate_text,
)

__all__ = [
    "ERROR_COLUMN",
    "FORMULA",
    "INSTRUCTION_SHEET",
    "MEDIA_TYPES",
    "RATE_CHOICES",
    "CellError",
    "Column",
    "Table",
    "TableRow",
    "TemplateError",
    "TemplateSpec",
    "build_csv",
    "build_file",
    "build_xlsx",
    "echo",
    "is_empty",
    "parse_day",
    "parse_key",
    "parse_money",
    "parse_rate",
    "rate_text",
    "read_table",
]

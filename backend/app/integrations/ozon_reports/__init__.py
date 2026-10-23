"""Отчёты Ozon, полученные через API (шаг 10.12): разбор ответа в тот же вид, что у XLSX-отчётов (NormalizedReportData)."""
from app.integrations.ozon_reports.realization import REALIZATION_SUFFIX, parse_realization_api

__all__ = ["REALIZATION_SUFFIX", "parse_realization_api"]

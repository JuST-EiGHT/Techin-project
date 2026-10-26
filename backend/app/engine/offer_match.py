"""Сопоставление артикулов (шаг 10.15): артикул из файла поставщика и артикул товара в Ozon часто пишутся по-разному —
регистр, пробелы, дефисы, точки, кириллица вместо латиницы («6001-2RS» и «6001 2rs», «НК1010» и «HK1010»).

normalize — ключ сравнения: без регистра, разделителей и с латиницей вместо похожих русских букв. suggest — похожие
артикулы каталога: совпал ключ → один содержит другой → похожи по написанию (difflib), сильнее похожие — первыми.
Только подсказка: привязывает пользователь, сами ничего не подменяем.
"""
from difflib import SequenceMatcher

# русские буквы, которые выглядят как латинские, — в латиницу
_LOOKALIKE = str.maketrans("авекмнорстух", "abekmhopctyx")
_SEPARATORS = str.maketrans("", "", " -_./\\,;: ‐‑‒–—")
MIN_RATIO = 0.75  # насколько похожи ключи, чтобы предложить
MIN_PART = 4  # «содержит» — только для ключей не короче


def normalize(offer: str) -> str:
    return offer.casefold().translate(_LOOKALIKE).translate(_SEPARATORS)


def suggest(alias: str, offers: list[str], limit: int = 3) -> list[str]:
    """Похожие артикулы каталога для артикула из файла; точное совпадение артикула — не подсказка (он и так известен)."""
    key = normalize(alias)
    if not key:
        return []
    scored: list[tuple[float, str]] = []
    for offer in offers:
        if offer == alias:
            continue
        other = normalize(offer)
        if not other:
            continue
        if other == key:
            score = 2.0
        elif min(len(key), len(other)) >= MIN_PART and (key in other or other in key):
            score = 1.0 + min(len(key), len(other)) / max(len(key), len(other)) / 2
        else:
            score = SequenceMatcher(None, key, other).ratio()
            if score < MIN_RATIO:
                continue
        scored.append((score, offer))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [offer for _, offer in scored[:limit]]

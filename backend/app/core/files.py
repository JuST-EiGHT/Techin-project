"""Ответ-файл для скачивания: русское имя — в filename* (RFC 5987), латиница — для старых браузеров."""
from urllib.parse import quote

from fastapi import Response


def attachment(content: bytes, media_type: str, name: str, fallback: str) -> Response:
    """name и fallback — с расширением: «Себестоимость.xlsx», «costs.xlsx»."""
    disposition = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})

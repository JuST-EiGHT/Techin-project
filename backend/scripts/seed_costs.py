"""Загрузить справочник себестоимости из файла (CSV/XLSX) для пользователя.

    docker compose exec api python -m scripts.seed_costs --email owner@example.com
    docker compose exec api python -m scripts.seed_costs --email owner@example.com --file /reference/cost_prices.csv

Тот же импорт, что и на сайте (services/cost_import.py): повторный запуск не создаёт дублей,
изменившиеся цены добавляются новой записью в историю, отчёты пользователя пересчитываются.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.user import User
from app.services.cost_import import CostImportError, ImportResult, import_costs

# reference/ в корне репозитория; в Docker смонтирован в /reference
DEFAULT_FILE = next(
    (
        p
        for p in (
            Path("/reference/cost_prices.csv"),
            Path(__file__).resolve().parents[2] / "reference" / "cost_prices.csv",
        )
        if p.exists()
    ),
    Path("/reference/cost_prices.csv"),
)


async def seed(sessions: async_sessionmaker[AsyncSession], email: str, path: Path) -> ImportResult:
    async with sessions() as db:
        user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
        if user is None:
            raise CostImportError(f"Пользователь {email} не найден — сначала зарегистрируйтесь на сайте.")
        return await import_costs(db, user.id, path.read_bytes(), path.name, apply=True, source="seed")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="email пользователя")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help=f"CSV или XLSX (по умолчанию {DEFAULT_FILE})")
    args = parser.parse_args(argv)
    if not args.file.exists():
        print(f"Файл {args.file} не найден", file=sys.stderr)
        return 1
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        result = await seed(async_sessionmaker(engine, expire_on_commit=False), args.email, args.file)
    except CostImportError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(
        f"Добавлено: {result.count('new')}, обновлено: {result.count('changed')}, без изменений: "
        f"{result.count('unchanged')}, ошибок: {result.count('error')}; пересчитано отчётов: {result.recosted_reports}"
    )
    for row in result.rows:
        if row.status == "error":
            print(f"  строка {row.line}: {row.message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

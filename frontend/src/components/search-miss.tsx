"use client";
// Поиск в таблице ничего не нашёл (правка 16.11): если товар есть в каталоге — сказать, что за период у него нет данных,
// и дать перейти к его статистике; иначе — по чему ищем. Каталог — тот же поиск, что у общего фильтра товара (GET /analytics/products:
// артикул, название, SKU, штрихкод).
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ProductOption } from "@/lib/analytics";

export function SearchMiss({
  search,
  what,
}: {
  search: string;
  /** «начислений Ozon», «продаж» — чего нет у товара за период */
  what: string;
}) {
  const [match, setMatch] = useState<ProductOption | null | undefined>(
    undefined,
  );
  useEffect(() => {
    let alive = true;
    setMatch(undefined);
    api
      .get<ProductOption[]>("/analytics/products", { params: { search } })
      .then(({ data }) => alive && setMatch(data[0] ?? null))
      .catch(() => alive && setMatch(null));
    return () => {
      alive = false;
    };
  }, [search]);
  if (match === undefined) return <span className="text-muted">Ищем…</span>;
  if (match === null)
    return (
      <span data-testid="search-miss">
        Ничего не нашлось. Ищем по артикулу, названию, SKU и штрихкоду.
      </span>
    );
  return (
    <span data-testid="search-miss-catalog">
      <b className="font-medium text-fg">{match.offer_id}</b>
      {match.name ? ` · ${match.name}` : ""} — товар есть в каталоге, но за этот
      период у него нет {what}.{" "}
      <Link
        href={`/dashboard/products/${match.id}`}
        className="text-accent hover:underline"
      >
        Статистика товара →
      </Link>
    </span>
  );
}

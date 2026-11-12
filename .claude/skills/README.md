# Скиллы проекта

Скиллы Claude Code, которые лежат в репозитории и подключаются в каждой сессии сами (облачная среда временная — всё,
что не в репозитории, пропадает).

| Скилл | Для чего | Источник | Лицензия |
|---|---|---|---|
| `impeccable` | Дизайн интерфейса: разбор (`critique`), проверка (`audit`), полировка (`polish`), раскладка, шрифт, тексты (`clarify`), адаптив, пустые состояния | [pbakaus/impeccable](https://github.com/pbakaus/impeccable), `plugin/skills/impeccable`, коммит `b4bff16` (11.11.2026), v4.8.2 | Apache 2.0 |
| `ui-ux-pro-max` | База правил UX с поиском: доступность, формы, навигация, таблицы, выбор графиков, советы под Next.js / Tailwind | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill), `.claude/skills/ui-ux-pro-max`, коммит `6ae86f6` (10.11.2026) | MIT |

## Русская адаптация

- **Правила проекта для обоих скиллов** — `impeccable/reference/project-ru.md`: что главнее чего, режимы страниц,
  токены темы, общие компоненты, данные на экране, русская типографика, как проверять, поправки к поведению скилла.
- **Переведены и адаптированы:** `impeccable/SKILL.md`, `impeccable/reference/craft-floor.md` (нижняя планка качества),
  `mode-operate.md` и `operate.md` (рабочие экраны — весь `/dashboard`), `clarify.md` (тексты интерфейса);
  `ui-ux-pro-max/SKILL.md` (плюс пути к скрипту — под установку в репозиторий).
- **Остались на английском:** остальные справочники impeccable (они загружаются только при вызове своей команды) и
  база ui-ux-pro-max (`data/*.csv`, `references/`) — поиск в ней идёт по английским словам.
- Не копировались тесты скрипта ui-ux-pro-max. Справочники impeccable для iOS и Android оставлены как в источнике, но к нашему веб-сервису не применяются.

## Как пользоваться

Скилл подключается сам, когда задача про интерфейс. Явно — командой:

```
/impeccable critique главная
/impeccable audit /dashboard/products
/impeccable polish components/home.tsx
/impeccable clarify тексты синхронизации
```

Поиск по базе: `python3 .claude/skills/ui-ux-pro-max/scripts/search.py "data table sort" --domain ux`.

Движок impeccable (`scripts/impeccable`) при первом запуске в сессии скачивает исполняемый файл с релизов GitHub автора
и сверяет SHA-256 с `impeccable/scripts/engine-checksums.txt`; файл кладётся в `~/.impeccable` и в репозиторий не
попадает (`scripts/bin/` — в `.gitignore`).

## Обновление

1. Скачать новую версию из источника, сравнить с текущей (`git diff --no-index`).
2. Перенести изменения в переведённые файлы вручную, сохранив пометки «*У нас:*» и ссылки на `project-ru.md`.
3. Обновить коммит и дату в этой таблице и в комментариях в начале обоих `SKILL.md`.

## Отложены

- `taste-skill` (leonxlnx) — для лендинга, шаг 8.3; добавляет хук, который проверяет страницу после каждого ответа.
- `stop-slop` (hardikpandya), `humanizer` (blader) — тексты лендинга; правила рассчитаны на английский.
- `diagram-design` (cathrynlavery) — схемы для документации, не для интерфейса.
- `understand-anything` (egonex-al) — репозиторий на GitHub недоступен.

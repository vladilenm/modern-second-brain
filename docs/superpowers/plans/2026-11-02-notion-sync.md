# Notion Sync Implementation Plan

> Выполняется в этом чате последовательно: тест → ожидаемое падение → реализация → проверка.

**Goal:** добавить автоматическое чтение всего доступного Notion в общий поиск Brain.

**Architecture:** `NotionConnector(ApiConnector)` использует REST API, сохраняет версии страниц
в существующем `source_cursor` и отдаёт `Batch` существующему sync. HTTP-клиент и коннектор
находятся в `brain/connectors/notion.py`, как у GitHub. Токен — в macOS Keychain.

**Tech Stack:** Python 3.9, urllib, unittest, SQLite; без новых зависимостей.

## Global Constraints

- API `2026-03-11`, Markdown v2, весь доступный workspace, включая «Личное» и «Архив».
- Только чтение Notion; не скачивать вложения, не удалять прежние локальные записи.
- Не печатать токены и серверные сообщения ошибок; не следовать HTTP-перенаправлениям.
- Пагинация списков, ограниченные повторы 429/529 с учётом Retry-After.
- Существующие коннекторы и схема SQLite сохраняются.

## Task 1: HTTP-клиент и импорт одной страницы

**Files:** создать `brain/connectors/notion.py`, `tests/test_notion.py`.

**Interfaces:** `NotionAPI(base, token).request(method, path, payload=None) -> dict`;
`NotionAPI.items(method, path, payload=None)` — пагинация; `NotionConnector.fetch(cursor)`.

- [x] Написать тест импорта: временный HTTPServer отвечает на `/search`, `/pages/{id}/markdown`,
  `/blocks/{id}/children`; реальный sync записывает title, Markdown, URL и kind=document.
  Проверка: `self.assertEqual(row['source'], 'notion')`, `self.assertIn('Поиск', row['body'])`.
- [x] Запустить `/usr/bin/python3 -m unittest discover -s tests -p test_notion.py`;
  убедиться в падении из-за отсутствующего коннектора.
- [x] Реализовать HTTP-клиент: Authorization, Notion-Version, таймаут, запрет редиректов,
  нормализованные ошибки; токен читать через существующий `keychain.password`.
- [x] Реализовать одну страницу как `Record(source='notion', external_id=page['id'],
  title=title, body=body, updated_at=page['last_edited_time'], location=page['url'], kind='document')`.
- [x] Проверить тесты источника и сохранить проверенный результат в Git.

## Task 2: Полный обход и повторные запуски

**Files:** `brain/connectors/notion.py`, `tests/test_notion.py`.

**Interfaces:** курсор `{'pages': {id: edited_time}, 'databases': [id], 'data_sources': [id],
'pending': [[kind, id]], 'partial_pages': {id: edited_time}}`.

- [x] Добавить тесты пагинации search/children/query, вложенных страниц и child_database.
  Строка базы находится только через `/data_sources/{id}/query`, не через поиск.
- [x] Добавить тесты: неизменившиеся страницы не скачиваются; изменение обновляет ту же запись;
  новая доступная страница со старой датой импортируется; обрыв не теряет записанные пачки.
- [x] Убедиться, что каждый новый сценарий падает до реализации.
- [x] Реализовать очередь UUID страниц/баз, обход вложенных блоков и запрос строк data sources.
  Сохранённые UUID участвуют в следующих запусках, даже если их нет в поиске.
- [x] Версию страницы сдвигать только после полного чтения, отдавать в одной пачке с Record.
  Неизменившиеся версии пропускать, новые UUID читать независимо от даты.
- [x] Добавить тест дочитывания unknown_block_ids Markdown, затем реализацию.
- [x] Прогнать тесты источника и сохранить проверенный результат.

## Task 3: Подключение к приложению и проверка

**Files:** `brain/connectors/__init__.py`, `brain/config.py`, `config.example.json`,
`tests/test_brain.py`, `brain/redact.py`, `README.md`.

- [x] Добавить ожидаемый источник notion к тесту RegistryTests; проверить падение.
- [x] Зарегистрировать NotionConnector; настройки: disabled, every_minutes=15, summarize=true,
  keychain_service=brain-notion, keychain_account=default, api_base=https://api.notion.com/v1.
- [x] Добавить тест маскировки токенов Notion, затем шаблоны к существующему redact.
- [x] Описать API-токен, Keychain и команды `brain sync --source notion --no-summarize`,
  `brain search 'текст' --source notion` в README и конфигурации-примере.
- [x] Запустить полный `/usr/bin/python3 -m unittest discover -s tests` с разрешением
  локальных HTTP-серверов и AppKit. Исходное состояние: 114 тестов проходят.
- [x] Проверить diff, отсутствие секретов и появление Notion в status/BrainBar.
- [ ] Подключить реальный токен через штатный Developer portal и Keychain; выполнить импорт,
  поиск известной заметки, повторный запуск. При отсутствии авторизации оставить конкретную
  локальную инструкцию подключения и сообщить, что импорт ждёт токен.

## Проверенный результат

137 тестов проходят, включая 23 теста Notion. Замечания ревью устранены. Источник зарегистрирован
в CLI, MCP и панели; BrainBar покажет его статистику после первого импорта.
Пользователь предоставил API-токен; он сохранён в Keychain, доступ к workspace проверен.
Источник включён, первый импорт идёт. Поиск по импортированным страницам и отображение в BrainBar
проверены. Реальный API потребовал обработки link_to_page через API блоков; добавлены регрессионные тесты.

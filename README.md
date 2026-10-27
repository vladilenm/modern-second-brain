# Второй мозг

Локальная поисковая база прошлых разговоров с ИИ: Claude Code, Codex, ChatGPT.
Скрипт забирает новые разговоры, чистит их от служебного шума, складывает в SQLite
и ищет по ним через BM25 с поправкой на свежесть. Claude подключён к базе через MCP.

Только стандартный Python 3.9 (`/usr/bin/python3`), без внешних зависимостей.

## Команды

```bash
bin/brain sync                 # забрать новое и пересказать очередь
bin/brain sync --no-summarize  # только забрать
bin/brain sync --rescan        # перечитать все файлы (после правки коннектора)
bin/brain search "ошибка бота клуба" -n 5
bin/brain show 42              # запись целиком (длинные — кусками, --offset)
bin/brain status               # сколько записей, живы ли коннекторы, очередь пересказа
bin/brain summarize --limit 10
bin/brain mcp                  # MCP-сервер (его запускает Claude сам)
```

## Как устроено

```
brain/connectors/   источники: каждый отвечает «что нового с прошлого раза»
  claude_code.py    ~/.claude/projects/*/*.jsonl (без subagents и служебных сообщений)
  codex.py          ~/.codex/sessions/**/rollout-*.jsonl (только разговоры с человеком)
  chatgpt_export.py архивы экспорта ChatGPT из папки inbox/
brain/sync.py       прогон коннекторов; файл перечитывается, только если изменился
                    и в нём 30 минут тишины (разговор закончен)
brain/db.py         SQLite: records + полнотекстовый индекс FTS5 (trigram)
brain/summarize.py  карточка от дешёвой модели: название, краткое содержание,
                    вопросы для поиска, ключевые слова, атомы (ошибки, команды, решения)
brain/search.py     BM25 с весами (карточка важнее сырого текста) × свежесть
brain/mcp_server.py инструменты search и get_record для Claude/Codex
data/brain.db       сама база (в git не попадает)
```

## Пересказ (нужна модель)

Скопируй `config.example.json` в `config.json` и впиши адрес OpenAI-совместимого API,
ключ и модель. `config.json` в git не попадает. Ключ можно не хранить в файле,
а передавать через переменную `BRAIN_LLM_API_KEY`.

Без модели всё работает, но поиск идёт только по сырому тексту разговоров.
Первый прогон — около 2 млн токенов на всю историю; дальше понемногу.
За один `sync` пересказывается `llm.batch_limit` записей (по умолчанию 40), свежие первыми.

## Исключённые проекты

Проекты, разговоры из которых в базу не нужны (например, автоматические прогоны бота),
перечисляются в `config.json`:

```json
"exclude_projects": ["~/Documents/dev/tbank-invest"]
```

Подпапки исключаются вместе с проектом. Новые разговоры оттуда `sync` пропускает,
а уже попавшие удаляет из базы и индекса при следующем запуске.

## ChatGPT

API для чтения истории у OpenAI нет. ChatGPT → Settings → Data controls → Export data,
архив из письма положить в `inbox/`. Следующий `sync` добавит новые и изменившиеся чаты.

## Новый источник

1. Файл в `brain/connectors/`: класс с `name`, методами `files()` и `parse(path) -> [Record]`.
2. Строка в `ALL` в `brain/connectors/__init__.py`.
3. Секция в `sources` в `brain/config.py`.

Курсор, дубли, индекс, пересказ и свежесть — общие, их писать не нужно.

## MCP

Подключён для всех проектов Claude Code и в Codex:

```bash
claude mcp add -s user brain -- /Users/vladilen/brain/bin/brain mcp
claude mcp remove brain -s user   # отключить в Claude Code
codex mcp add brain -- /Users/vladilen/brain/bin/brain mcp
codex mcp remove brain            # отключить в Codex
```

Оба инструмента помечены как «только чтение» (`readOnlyHint`), поэтому Codex вызывает их
без запроса разрешения.

## Автозапуск

`brain sync` раз в 15 минут через launchd, лог — `~/Library/Logs/brain-sync.log`.
Проект лежит в `~/brain`, а не в «Документах»: macOS не пускает фоновые задачи launchd
в защищённые папки (Документы, Рабочий стол, Загрузки) — там агент падает с «Operation not permitted».

```bash
cp launchd/com.vladilen.brain-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vladilen.brain-sync.plist   # включить
launchctl bootout gui/$(id -u)/com.vladilen.brain-sync                                    # выключить
rm ~/Library/LaunchAgents/com.vladilen.brain-sync.plist                                   # убрать совсем
launchctl kickstart gui/$(id -u)/com.vladilen.brain-sync                                  # прогнать сейчас
```

## Значок в строке меню

`~/Applications/BrainBar.app` — мозг в строке меню. Каждые 2 секунды читает `data/status.json`,
который пишут `brain sync` и `brain summarize`:

- без подписи — всё синхронизировано;
- `↻` — забирает новые разговоры;
- `12/40` — пересказывает 12-ю запись из 40;
- `!` — прогон упал или прервался, либо синхронизации не было больше 45 минут;
- `?` — синхронизация ещё ни разу не запускалась.

В меню: время последней и следующей синхронизации, сколько записей по источникам и сколько ждут
пересказа, последние карточки (открываются текстом), «Синхронизировать сейчас», «Открыть лог»
и переключатель «Запускать при входе в систему».

```bash
menubar/build.sh                                            # собрать, установить, перезапустить
~/Applications/BrainBar.app/Contents/MacOS/BrainBar --dump  # текст меню без интерфейса
```

## Панель базы

`bin/brain dashboard --open` (или «Открыть базу» в значке) собирает `data/dashboard.html` и открывает
в браузере: сводка, источники (сколько записей, сколько ещё идут, сколько служебных файлов пропущено),
разговоры по неделям, проекты и все записи с фильтрами. По клику на запись — карточка и начало расшифровки.
Это снимок на момент сборки: данные вшиты в страницу, сервер не нужен. В файле то же, что в базе,
поэтому он в `data/` (в git не попадает) с правами 600.

## Как это работает

Ни Docker, ни сервера базы нет. База — один файл SQLite `data/brain.db`, его читает и пишет тот,
кому он нужен в данный момент:

- `brain sync` раз в 15 минут запускает launchd: процесс забирает новое, пересказывает очередь и завершается;
- MCP-сервер `brain` запускает сам Claude, когда открывается сессия, и закрывает вместе с ней;
- BrainBar — маленькое приложение в строке меню, оно только читает `data/status.json`.

## Тесты

```bash
/usr/bin/python3 -m unittest discover -s tests
```

"""Notion REST API: страницы и строки баз как документы, токен из macOS Keychain.

Курсор хранит версии отдельных страниц: новые доступные заметки со старой датой
не теряются, а оборванный прогон продолжает с последней записанной пачки.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from typing import Iterator, Optional

from .. import keychain
from ..redact import CARD_MASK, MASK
from .base import ApiConnector, Batch, Record

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2026-03-11"
REQUEST_INTERVAL = 0.35
PAGE_BATCH = 20
MAX_RESPONSE = 32 * 1024 * 1024
FILE_URL = re.compile(
    r'https://(?:prod-files-secure\.s3\.us-west-2\.amazonaws\.com/'
    r'|s3\.us-west-2\.amazonaws\.com/secure\.notion-static\.com/|secure\.notion-static\.com/)'
    r'(?:' + re.escape(MASK) + '|' + re.escape(CARD_MASK) + r'|[^\s<>"\')])+'
)


def normalize_file_urls(text: str) -> str:
    """Стабильные адреса вложений для индекса, без временной подписи загрузки Notion.

    Подпись меняется при каждом чтении и не означает правку документа. Файлы открывают
    через исходную страницу: подписанные ссылки из API всё равно истекают через час.
    Маски учитываем для нормализации уже сохранённых локальных записей.
    """
    def stable(match):
        url = urllib.parse.urlsplit(match.group())
        params = url.query.split("&")
        kept = [p for p in params if not p.split("=", 1)[0].lower().startswith("x-amz-")]
        return url._replace(query="&".join(kept)).geturl() if kept != params else match.group()

    return FILE_URL.sub(stable, text)


class Inaccessible(RuntimeError):
    """404: страницу удалили или закрыли доступ к ней."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def object_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("Notion: API вернул некорректный ID объекта") from None


class NotionAPI:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")) or (
                not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("Notion: api_base должен быть адресом HTTPS")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)
        self.last_request = 0.0

    def request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        headers = {"Authorization": "Bearer " + self.token, "Notion-Version": API_VERSION,
                   "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "brain-sync"}
        body = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(3):
            delay = REQUEST_INTERVAL - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
                with self.opener.open(request, timeout=30) as response:
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise RuntimeError("Notion: ответ API слишком большой")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError()
                return data
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry = exc.headers.get("Retry-After", "1")
                exc.close()
                if code == 404:
                    raise Inaccessible("Notion: страница недоступна (HTTP 404)") from None
                if code in (429, 529):
                    try:
                        delay = max(1, int(retry))
                    except ValueError:
                        delay = 1
                    if attempt < 2 and delay <= 60:
                        time.sleep(delay * (2 ** attempt))
                        continue
                    raise RuntimeError(f"Notion: лимит запросов (HTTP {code}), повторим в следующий запуск") from None
                if code == 401:
                    raise RuntimeError("Notion: токен недействителен или истёк (HTTP 401); обнови его в связке ключей") from None
                if code == 403:
                    raise RuntimeError("Notion: нет права Read content или доступа к странице (HTTP 403)") from None
                raise RuntimeError(f"Notion: ошибка API (HTTP {code})") from None
            except (urllib.error.URLError, OSError):
                raise RuntimeError("Notion: нет связи с API, повторим в следующий запуск") from None
            except (ValueError, UnicodeError):
                raise RuntimeError("Notion: API вернул некорректный JSON") from None

    def items(self, method: str, path: str, payload: Optional[dict] = None) -> Iterator[dict]:
        for page in self.pages(method, path, payload):
            yield from page["results"]

    def pages(self, method: str, path: str, payload: Optional[dict] = None) -> Iterator[dict]:
        params = dict(payload or {}, page_size=100)
        seen = set()
        while True:
            url = path + "?" + urllib.parse.urlencode(params) if method == "GET" else path
            data = self.request(method, url, None if method == "GET" else params)
            if not isinstance(data.get("results"), list):
                raise RuntimeError("Notion: API вернул некорректный список")
            status = data.get("request_status") or {}
            if status.get("type") == "incomplete" or status.get("incomplete_reason"):
                raise RuntimeError("Notion: API вернул неполный список; повторим в следующий запуск")
            yield data
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError("Notion: API не продвинул пагинацию")
            seen.add(cursor)
            params["start_cursor"] = cursor


def rich_text(items) -> str:
    if isinstance(items, dict):
        items = [items]  # property_item внутри rollup содержит одно значение, страница — список
    return "".join(item.get("plain_text", (item.get("text") or {}).get("content", "")) for item in items or [])


def property_text(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind)
    if value is None:
        return ""
    if kind in ("title", "rich_text"):
        return rich_text(value)
    if kind in ("select", "status"):
        return value.get("name", "")
    if kind in ("multi_select", "people", "files"):
        if isinstance(value, dict):
            value = [value]
        return ", ".join(v.get("name") or v.get("id", "") for v in value)
    if kind == "date":
        return " — ".join(value[k] for k in ("start", "end") if value.get(k))
    if kind in ("checkbox", "boolean"):
        return "да" if value else "нет"
    if kind in ("string", "number", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return str(value)
    if kind == "relation":
        if isinstance(value, dict):
            value = [value]
        return ", ".join("https://www.notion.so/" + object_id(v["id"]) for v in value)
    if kind in ("formula", "rollup") and isinstance(value, dict):
        return property_text(value) if value.get("type") != "array" else "; ".join(property_text(v) for v in value.get("array", []))
    return ""


class NotionConnector(ApiConnector):
    name = source = "notion"
    title = "Notion"
    about = "страницы, заметки и записи баз в Notion"
    default_every_minutes = 15
    empty_hint = "Подключи API-токен Notion в связке ключей (README, раздел «Notion»)."

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        service = self.settings.get("keychain_service") or "brain-notion"
        account = self.settings.get("keychain_account") or "default"
        try:
            secret = keychain.password(service, account)
        except RuntimeError:
            raise RuntimeError("Notion: нет API-токена; создай его в Developer portal и сохрани командой `" +
                               keychain.add_command(service, account) + "`") from None
        api = NotionAPI(self.settings.get("api_base") or API_BASE, secret)
        try:
            pos = json.loads(cursor) if cursor else {}
        except ValueError:
            pos = {}
        if not isinstance(pos, dict) or not isinstance(pos.get("pages", {}), dict):
            pos = {}
        yield from self._pass(api, pos)

    def _pass(self, api: NotionAPI, pos: dict) -> Iterator[Batch]:
        versions = pos.setdefault("pages", {})
        partial = pos.setdefault("partial_pages", {})
        databases = set(pos.get("databases") or [])
        sources = set(pos.get("data_sources") or [])
        # Повторное обнаружение не зависит от даты: вчера могли открыть доступ к старой заметке.
        queue = deque((item.get("object"), object_id(item["id"]), item)
                      for item in api.items("POST", "/search"))
        queue.extend((kind, oid, None) for kind, oid in pos.get("pending", []))
        queue.extend(("data_source", sid, None) for sid in sorted(sources))
        queue.extend(("database", did, None) for did in sorted(databases))
        queue.extend(("page", pid, None) for pid in versions)
        seen = set()
        deferred = set()
        batch = []
        imported = 0

        def checkpoint():
            pending = sorted(deferred | {(kind, oid) for kind, oid, _ in queue if (kind, oid) not in seen})
            pos.update(databases=sorted(databases), data_sources=sorted(sources), pending=pending)
            return json.dumps(pos, sort_keys=True)

        while queue:
            kind, oid, item = queue.popleft()
            oid = object_id(oid)
            if (kind, oid) in seen and (kind != "page" or item is None):
                continue
            seen.add((kind, oid))
            try:
                if kind == "database":
                    databases.add(oid)
                    database = api.request("GET", f"/databases/{oid}")
                    queue.extend(("data_source", object_id(s["id"]), None) for s in database.get("data_sources", []))
                    continue
                if kind == "data_source":
                    sources.add(oid)
                    queue.extend((r.get("object", "page"), object_id(r["id"]), r)
                                 for r in api.items("POST", f"/data_sources/{oid}/query"))
                    continue
                if kind != "page":
                    continue
                item = item or api.request("GET", f"/pages/{oid}")
                if item.get("in_trash") or item.get("archived") or item.get("is_archived"):
                    continue
                edited = item.get("last_edited_time")
                if not isinstance(edited, str) or not edited:
                    raise RuntimeError("Notion: у страницы нет даты изменения")
                # Search может отставать от query базы: старый снимок не должен откатывать версию.
                if versions.get(oid, "") >= edited:
                    continue
                markdown, content_complete = self._markdown(api, oid)
                children, discovery_complete = self._children(api, oid)
                queue.extend(children)
                item = dict(item, properties=self._properties(api, item))
            except Inaccessible:
                deferred.add((kind, oid))
                continue  # локальную копию сохраняем; закрытая страница не останавливает другие
            batch.append(self._record(item, markdown))
            repeated_partial = partial.get(oid) == edited
            if content_complete and discovery_complete:
                versions[oid] = edited
                partial.pop(oid, None)
            else:
                deferred.add(("page", oid))  # доступ к вложенному блоку может измениться без правки родителя
                partial[oid] = edited
            # Повтор той же частичной заметки не должен занимать всю пачку и блокировать новые.
            imported += not repeated_partial or (content_complete and discovery_complete)
            if len(batch) >= PAGE_BATCH:
                yield Batch(batch, checkpoint())
                batch = []
            if imported >= max(1, int(self.settings.get("max_pages_per_run", 100))):
                break  # первая загрузка растянется на несколько запусков, версии уже сохранены
        yield Batch(batch, checkpoint())

    @staticmethod
    def _properties(api: NotionAPI, item: dict) -> dict:
        properties = dict(item.get("properties") or {})
        for name, prop in properties.items():
            kind = prop.get("type")
            values = prop.get(kind)
            paginated = kind in ("title", "rich_text", "people", "relation")
            omitted = prop.get("has_more") or (isinstance(values, list) and len(values) >= 25)
            if not prop.get("id") or not ((paginated and omitted) or kind in ("formula", "rollup")):
                continue
            prop_id = urllib.parse.quote(urllib.parse.unquote(prop["id"]), safe="")
            pid = object_id(item["id"])
            path = f"/pages/{pid}/properties/{prop_id}"
            if kind == "formula":
                properties[name] = api.request("GET", path)
                continue
            rows = []
            summary = {}
            for page in api.pages("GET", path):
                rows.extend(page["results"])
                summary = page.get("property_item") or {}
            if kind == "rollup":
                value = summary.get("rollup") or {}
                if value.get("type") in (None, "incomplete"):
                    raise RuntimeError("Notion: API не закончил вычисление свойства rollup")
                if value["type"] == "array":
                    value = dict(value, array=rows)
                properties[name] = {"type": "rollup", "rollup": value}
            else:
                properties[name] = {"type": kind, kind: [r[kind] for r in rows]}
        return properties

    @staticmethod
    def _record(item: dict, markdown: str) -> Record:
        properties = item.get("properties") or {}
        title = next((property_text(p) for p in properties.values() if p.get("type") == "title"), "")
        props = [f"{name}: {property_text(p)}" for name, p in properties.items()
                 if p.get("type") != "title" and property_text(p)]
        body = normalize_file_urls("\n\n".join(s for s in ("\n".join(props), markdown) if s))
        return Record("notion", object_id(item["id"]), title or "Без названия", body, item["last_edited_time"],
                      started_at=item.get("created_time"), location=item.get("url"), kind="document")

    @staticmethod
    def _children(api: NotionAPI, page_id: str):
        blocks = [page_id]
        seen = set()
        found = []
        complete = True
        while blocks:
            bid = blocks.pop()
            if bid in seen:
                continue
            seen.add(bid)
            try:
                children = list(api.items("GET", f"/blocks/{bid}/children"))
            except Inaccessible:
                if bid == page_id:
                    raise
                complete = False
                continue  # например, исходный блок синхронизирован из недоступной страницы
            for block in children:
                if block.get("archived") or block.get("in_trash"):
                    continue
                kind = block.get("type")
                oid = object_id(block["id"])
                if kind == "child_page":
                    found.append(("page", oid, None))
                elif kind == "child_database":
                    found.append(("database", oid, None))
                elif block.get("has_children"):
                    blocks.append(oid)
        return found, complete

    @staticmethod
    def _markdown(api: NotionAPI, page_id: str):
        remaining = [page_id]
        seen = set()
        parts = []
        complete = True
        while remaining:
            oid = remaining.pop()
            if oid in seen:
                complete = False
                continue  # циклический экспорт не должен останавливать остальные страницы
            seen.add(oid)
            try:
                result = api.request("GET", f"/pages/{oid}/markdown?markdown_version=v2")
            except Inaccessible:
                if oid == page_id:
                    raise
                parts.append("[Вложенный блок недоступен для чтения]")
                complete = False
                continue
            if not isinstance(result.get("markdown"), str):
                raise RuntimeError("Notion: API не вернул Markdown страницы")
            text = result["markdown"]
            unknown = [object_id(bid) for bid in result.get("unknown_block_ids") or []]
            if result.get("truncated") and not unknown:
                raise RuntimeError("Notion: неполная страница без списка непрочитанных блоков")
            if oid in unknown:
                # link_to_page возвращается как unknown со своим же ID даже при полном доступе.
                # Структурированный API умеет читать эту ссылку, Markdown API — нет.
                try:
                    block = api.request("GET", f"/blocks/{oid}")
                except Inaccessible:
                    block = {}
                link = block.get("link_to_page") or {}
                target = link.get(link.get("type"))
                if block.get("type") == "link_to_page" and target:
                    text = "Ссылка на страницу: https://www.notion.so/" + object_id(target)
                else:
                    complete = False
                unknown = [bid for bid in unknown if bid != oid]
            parts.append(text)
            remaining.extend(reversed(unknown))
        return "\n\n".join(parts), complete

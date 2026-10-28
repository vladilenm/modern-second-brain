"""GitHub: обсуждения (PR и issues) с участием пользователя, коммиты по месяцам и README его репозиториев.

Токен берём у gh (`gh auth token`) — новых секретов нет. Запросы — urllib к REST API.
Курсор — JSON {"issues": updated_at, "repos": pushed_at}: до какого места забраны обсуждения
и репозитории. Граница включительная: последнюю запись прошлого прогона берём ещё раз, хеш отсечёт дубль.
Пока проход по репозиториям не закончен, в курсоре ещё лежит `repos_since` — месяц его начала:
оборвавшийся прогон продолжит с того же месяца.
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional, Tuple

from ..timeutil import to_iso
from .base import ApiConnector, Batch, Record, clip

DISCUSSION_BATCH = 20
# launchd запускает sync с PATH=/usr/bin:/bin:/usr/sbin:/sbin — Homebrew туда не попадает.
GH_FALLBACKS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
REVIEW_STATE = {"APPROVED": "одобрено", "CHANGES_REQUESTED": "нужны правки", "DISMISSED": "отозвано"}
TRAILER = re.compile(r"^[ \t]*Co-Authored-By:.*\n?", re.I | re.M)
MAX_COMMIT_CHARS = 4000
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
          "сентябрь", "октябрь", "ноябрь", "декабрь")


class NotFound(Exception):
    """404 или 409: README нет или репозиторий пустой — не ошибка, просто нет записи."""


class Fatal(RuntimeError):
    """Нет доступа, лимит или связи — дальше в этом прогоне идти бессмысленно."""


def gh_token_command() -> List[str]:
    path = shutil.which("gh") or next((p for p in GH_FALLBACKS if os.path.exists(p)), None)
    if not path:
        raise RuntimeError("GitHub: не найден gh — поставь его (brew install gh) и выполни gh auth login")
    return [path, "auth", "token"]


def read_token(command: Optional[List[str]]) -> str:
    try:
        out = subprocess.run(command or gh_token_command(), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"GitHub: не получилось взять токен ({exc}), выполни gh auth login") from None
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        raise RuntimeError("GitHub: нет доступа, выполни gh auth login")
    return token


def login_of(obj: Optional[dict]) -> str:
    # Удалённые аккаунты GitHub отдаёт как user: null.
    return (obj or {}).get("login") or "ghost"


def month_start(iso: str, back: int = 0) -> str:
    """Первое число месяца, на `back` месяцев раньше месяца из iso: 2026-10-20T12:00:00Z → 2026-10-01T00:00:00Z."""
    index = int(iso[:4]) * 12 + int(iso[5:7]) - 1 - back
    return f"{index // 12:04d}-{index % 12 + 1:02d}-01T00:00:00Z"


class GitHubAPI:
    """Тонкая обёртка над REST API: авторизация, страницы по заголовку Link и понятные ошибки."""

    def __init__(self, base: str, token: str, timeout: float = 30):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, url: str) -> Tuple[object, str]:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "brain-sync",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return (json.loads(raw) if raw else None), resp.headers.get("Link") or ""
        except urllib.error.HTTPError as exc:
            exc.close()  # иначе сокет ответа висит до сборки мусора
            if exc.code in (404, 409):
                raise NotFound(url) from None
            if exc.code == 401:
                raise Fatal("GitHub: нет доступа, выполни gh auth login") from None
            if exc.code == 429 or (exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0"):
                raise Fatal("GitHub: исчерпан лимит запросов, продолжим в следующий прогон") from None
            raise RuntimeError(f"GitHub: HTTP {exc.code} на {url}") from None
        except (urllib.error.URLError, OSError) as exc:
            # нет сети, таймаут, обрыв соединения
            raise Fatal(f"GitHub: нет связи ({getattr(exc, 'reason', exc)})") from None

    def _url(self, path: str, params: Optional[dict] = None) -> str:
        return self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")

    def get(self, path: str, params: Optional[dict] = None):
        return self._request(self._url(path, params))[0]

    def items(self, path: str, params: Optional[dict] = None) -> Iterator[dict]:
        """Все элементы списка по страницам; у поиска они лежат в items."""
        url = self._url(path, dict(params or {}, per_page=100))
        while url:
            data, link = self._request(url)
            yield from ((data.get("items") or []) if isinstance(data, dict) else (data or []))
            match = NEXT_LINK.search(link)
            url = match.group(1) if match else None


class GitHubConnector(ApiConnector):
    name = "github"
    source = "github"
    title = "GitHub"
    about = "PR, issues, коммиты и README на GitHub"

    def excludes(self, project: Optional[str]) -> bool:
        patterns = self.settings.get("exclude_repos") or []
        return bool(project) and any(fnmatch.fnmatchcase(project.lower(), p.lower()) for p in patterns)

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        api = GitHubAPI(self.settings.get("api_base") or "https://api.github.com",
                        read_token(self.settings.get("token_command")))
        login = api.get("/user")["login"]
        pos = self._position(cursor)
        failed = None
        try:
            yield from self._discussions(api, login, pos)
        except Fatal:
            raise
        except RuntimeError as exc:
            # Обсуждения упали — коммиты и README всё равно забираем, ошибку покажем в конце прогона.
            failed = exc
        yield from self._repos(api, login, pos)
        if failed:
            raise failed

    def _position(self, cursor: Optional[str]) -> dict:
        start = to_iso((datetime.now(timezone.utc) - timedelta(days=self.backfill_days)).timestamp())
        pos = {"issues": start, "repos": start}
        if cursor:
            try:
                pos.update(json.loads(cursor))
            except ValueError:
                pass  # битый курсор — начинаем заново, дубли отсечёт хеш
        return pos

    def _discussions(self, api: GitHubAPI, login: str, pos: dict) -> Iterator[Batch]:
        # По возрастанию updated: поиск отдаёт не больше 1000 результатов за запрос,
        # остаток заберёт следующий прогон с курсора.
        found = api.items("/search/issues", {"q": f"involves:{login} updated:>={pos['issues']}",
                                             "sort": "updated", "order": "asc"})
        start = saved = pos["issues"]  # saved — курсор, который реально ушёл в пачке
        batch: List[Record] = []
        try:
            for item in found:
                repo = item["repository_url"].split("/repos/", 1)[1]
                if not self.excludes(repo):
                    try:
                        batch.append(self._discussion(api, repo, item))
                    except NotFound:
                        # Обсуждение пропало вместе с репозиторием — не повод стопорить источник.
                        pass
                    except Fatal:
                        raise
                    except RuntimeError as exc:
                        raise RuntimeError(f"{exc} — обсуждение {repo}#{item['number']}; если ошибка повторяется, "
                                           f"добавь {repo} в exclude_repos") from None
                pos["issues"] = item["updated_at"]
                if len(batch) >= DISCUSSION_BATCH:
                    yield Batch(batch, json.dumps(pos, sort_keys=True))
                    saved = pos["issues"]
                    batch = []
            # Курсор сохраняем и тогда, когда хвост прогона весь отфильтрован: иначе он переберётся заново.
            if batch or pos["issues"] != start:
                yield Batch(batch, json.dumps(pos, sort_keys=True))
                saved = pos["issues"]
        except Exception:
            # Несохранённые обсуждения не должны уйти в курсор следующих пачек (репозиториев).
            pos["issues"] = saved
            raise

    def _discussion(self, api: GitHubAPI, repo: str, item: dict) -> Record:
        number = item["number"]
        is_pr = "pull_request" in item
        events = []  # (когда, текст): комментарии, ревью и замечания к коду вперемешку, по времени
        for c in api.items(f"/repos/{repo}/issues/{number}/comments"):
            events.append((c["created_at"],
                           f"{login_of(c.get('user'))} ({c['created_at'][:10]}): {c.get('body') or ''}"))
        if is_pr:
            for r in api.items(f"/repos/{repo}/pulls/{number}/reviews"):
                state = REVIEW_STATE.get(r.get("state"))
                if not (r.get("body") or state):
                    continue  # пустое ревью — обёртка над замечаниями к строкам, они ниже
                when = r.get("submitted_at") or item["updated_at"]
                verdict = f", ревью «{state}»" if state else ", ревью"
                events.append((when, f"{login_of(r.get('user'))} ({when[:10]}){verdict}: {r.get('body') or ''}"))
            for c in api.items(f"/repos/{repo}/pulls/{number}/comments"):
                events.append((c["created_at"], f"{login_of(c.get('user'))} ({c['created_at'][:10]}) "
                                                f"к {c.get('path')}: {c.get('body') or ''}"))
        events.sort(key=lambda e: e[0])
        merged = (item.get("pull_request") or {}).get("merged_at")
        status = "влит" if merged else ("открыт" if item["state"] == "open" else "закрыт")
        head = (f"{'PR' if is_pr else 'Issue'} {repo}#{number}: {item['title']}\n"
                f"Статус: {status}. Автор: {login_of(item.get('user'))}. "
                f"Создан {item['created_at'][:10]}, обновлён {item['updated_at'][:10]}.")
        parts = [head, (item.get("body") or "").strip()] + [text.strip() for _, text in events]
        return Record(
            source=self.source,
            external_id=f"{'pr' if is_pr else 'issue'}:{repo}#{number}",
            title=f"{repo}#{number}: {item['title']}",
            body="\n\n".join(clip(p) for p in parts if p),
            updated_at=to_iso(item["updated_at"]),
            started_at=to_iso(item["created_at"]),
            location=item.get("html_url"),
            project=repo,
            kind="discussion",
        )

    def _repos(self, api: GitHubAPI, login: str, pos: dict) -> Iterator[Batch]:
        """Репозитории, в которые пушили после курсора, по одному на пачку: их месяцы коммитов и README."""
        # Пока проход по репозиториям не закончен, нижняя граница хранится в курсоре: оборвался
        # посередине — следующий прогон доберёт остальные репозитории с того же месяца.
        # Месяцы собираем заново целиком и начинаем с месяца до курсора: курсор — самый поздний push,
        # а коммиты конца прошлого месяца могли запушиться уже в новом, иначе они бы не попали.
        since = pos.get("repos_since") or month_start(pos["repos"], back=1)
        pos["repos_since"] = since
        repos = [r for r in api.items("/user/repos", {"affiliation": "owner,collaborator,organization_member"})
                 if not r.get("fork") and (r.get("pushed_at") or "") >= pos["repos"]
                 and not self.excludes(r["full_name"])]
        for repo in sorted(repos, key=lambda r: r["pushed_at"]):
            try:
                records = self._commit_months(api, login, repo, since)
                readme = self._readme(api, repo)
            except Fatal:
                raise
            except RuntimeError as exc:
                raise RuntimeError(f"{exc} — репозиторий {repo['full_name']}; "
                                   "если ошибка повторяется, добавь его в exclude_repos") from None
            if readme:
                records.append(readme)
            pos["repos"] = repo["pushed_at"]
            yield Batch(records, json.dumps(pos, sort_keys=True))
        pos.pop("repos_since")
        yield Batch([], json.dumps(pos, sort_keys=True))  # проход закончен — граница снова от курсора

    def _commit_months(self, api: GitHubAPI, login: str, repo: dict, since: str) -> List[Record]:
        name = repo["full_name"]
        months = {}  # "2026-10" → [(когда, короткий хеш, сообщение)]
        try:
            for c in api.items(f"/repos/{name}/commits", {"author": login, "since": since}):
                when = to_iso(((c.get("commit") or {}).get("committer") or {}).get("date"))
                if when:
                    message = TRAILER.sub("", c["commit"].get("message") or "").strip()
                    months.setdefault(when[:7], []).append((when, c["sha"][:7], message))
        except NotFound:
            return []  # пустой репозиторий
        records = []
        for month, commits in sorted(months.items()):
            commits.sort()
            entries = []
            for when, sha, message in commits:
                subject, _, rest = message.partition("\n")
                entry = f"{when[:10]} {sha} {subject}"
                if rest.strip():
                    entry += "\n" + clip(rest.strip(), MAX_COMMIT_CHARS)
                entries.append(entry)
            year, mon = month.split("-")
            records.append(Record(
                source=self.source,
                external_id=f"commits:{name}:{month}",
                title=f"{name} — коммиты за {MONTHS[int(mon) - 1]} {year}",
                body="\n\n".join(entries),
                updated_at=commits[-1][0],
                started_at=commits[0][0],
                location=f"{repo['html_url']}/commits",
                project=name,
                kind="changelog",
            ))
        return records

    def _readme(self, api: GitHubAPI, repo: dict) -> Optional[Record]:
        name = repo["full_name"]
        try:
            data = api.get(f"/repos/{name}/readme")
        except NotFound:
            return None
        text = base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace").strip()
        head = [f"Репозиторий {name}" + (" (приватный)" if repo.get("private") else "")]
        if repo.get("description"):
            head.append(repo["description"])
        if repo.get("topics"):
            head.append("Темы: " + ", ".join(repo["topics"]))
        return Record(
            source=self.source,
            external_id=f"readme:{name}",
            title=f"{name} — README",
            body="\n".join(head) + "\n\n" + clip(text),
            # Если README не менялся, хеш тот же и запись не трогается.
            updated_at=to_iso(repo["pushed_at"]),
            started_at=to_iso(repo.get("created_at")),
            location=repo.get("html_url"),
            project=name,
            kind="document",
        )

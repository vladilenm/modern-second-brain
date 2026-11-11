"""YouTube: видео своих каналов со статистикой и комментариями зрителей.

Официальный YouTube Data API v3 с ключом из связки ключей: только публичные данные, вход в аккаунт
не нужен. Одно видео — одна запись. Расшифровку речи не берём.

Курсор — JSON {"videos": {id: когда запись обновляли}}. Список видео и статистику читаем каждый
прогон (это дёшево), а запись с комментариями обновляем по возрасту видео: свежие — раз в сутки,
до 90 дней — раз в неделю, старые — раз в 90 дней. Числа статистики округляем, иначе запись менялась бы
каждый прогон и заново уходила на пересказ.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional, Tuple

from .. import keychain
from ..timeutil import parse, to_iso
from .base import ApiConnector, Batch, Record, clip

API_BASE = "https://www.googleapis.com/youtube/v3"
BATCH = 20
PAGE = 50                  # столько идентификаторов видео принимает videos.list
MAX_COMMENT_CHARS = 1000
MAX_DESCRIPTION_CHARS = 4000
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
# (возраст видео до, как часто обновлять запись)
SCHEDULE = ((timedelta(days=7), timedelta(days=1)), (timedelta(days=90), timedelta(days=7)))
OLD_EVERY = timedelta(days=90)
SLACK = timedelta(hours=1)  # прогон чуть раньше срока не должен пропускать обновление


class Fatal(RuntimeError):
    """Нет ключа, квоты или связи — дальше в этом прогоне идти бессмысленно."""


class CommentsDisabled(Exception):
    """Комментарии под видео отключены — не ошибка, запись идёт без них."""


def channel_query(spec: str) -> dict:
    """«@handle», ссылка на канал или идентификатор UC… → параметр запроса channels.list."""
    value = (spec or "").strip()
    if value.startswith(("http://", "https://")):
        parts = [p for p in urllib.parse.urlsplit(value).path.split("/") if p]
        value = parts[1] if len(parts) > 1 and parts[0] == "channel" else (parts[0] if parts else "")
    if CHANNEL_ID.match(value):
        return {"id": value}
    if value.startswith("@") and len(value) > 1:
        return {"forHandle": value}
    raise RuntimeError(f"YouTube: не понял канал «{spec}» — укажи @handle, ссылку на канал или идентификатор UC…")


def rounded(value) -> str:
    """Два значащих разряда: 12 345 → «около 12 тыс.». Числа до 100 — как есть."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "—"
    if n < 100:
        return str(n)
    digits = len(str(n))
    n2 = int(round(n, 2 - digits))
    if n2 < 1000:
        text = str(n2)
    elif n2 < 1_000_000:
        text = f"{n2 / 1000:g} тыс.".replace(".", ",", 1) if n2 % 1000 else f"{n2 // 1000} тыс."
    else:
        text = f"{n2 / 1_000_000:g} млн".replace(".", ",", 1)
    return "около " + text  # всегда с «около»: иначе 12 000 и 12 001 давали бы разный текст


def duration_text(iso: Optional[str]) -> str:
    m = DURATION.match(iso or "")
    if not m:
        return ""
    h, mnt, s = (int(x or 0) for x in m.groups())
    return f"{h}:{mnt:02d}:{s:02d}" if h else f"{mnt}:{s:02d}"


def due(published: Optional[datetime], refreshed: Optional[datetime], now: datetime) -> bool:
    """Пора ли обновить запись видео: чем старше видео, тем реже."""
    if refreshed is None:
        return True
    age = now - published if published else OLD_EVERY * 2
    every = next((e for limit, e in SCHEDULE if age < limit), OLD_EVERY)
    return now - refreshed >= every - SLACK


class YouTubeAPI:
    def __init__(self, base: str, key: str, timeout: float = 30):
        self.base = base.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")) or (
                not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("YouTube: api_base должен быть адресом HTTPS")
        self.key = key
        self.timeout = timeout

    def get(self, path: str, params: dict) -> dict:
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        # Ключ идёт заголовком, а не в адресе: адрес может попасть в сообщение об ошибке.
        req = urllib.request.Request(url, headers={"X-Goog-Api-Key": self.key, "Accept": "application/json",
                                                   "User-Agent": "brain-sync"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", "ignore") or "{}")
            except ValueError:
                body = {}
            exc.close()
            error = body.get("error") if isinstance(body, dict) else None
            reasons = {e.get("reason") for e in (error or {}).get("errors", []) if isinstance(e, dict)}
            if "commentsDisabled" in reasons:
                raise CommentsDisabled() from None
            if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
                raise Fatal("YouTube: квота API исчерпана до завтра, продолжим в следующий прогон") from None
            if exc.code in (400, 401, 403):
                raise Fatal(f"YouTube: API отклонил запрос (HTTP {exc.code}) — проверь ключ и что для него "
                            "включён YouTube Data API v3") from None
            raise RuntimeError(f"YouTube: HTTP {exc.code} на {path}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise Fatal(f"YouTube: нет связи ({getattr(exc, 'reason', exc)})") from None
        except ValueError:
            raise RuntimeError(f"YouTube: непонятный ответ на {path}") from None
        if not isinstance(data, dict):
            raise RuntimeError(f"YouTube: непонятный ответ на {path}")
        return data

    def pages(self, path: str, params: dict) -> Iterator[dict]:
        token, seen = None, set()
        while True:
            data = self.get(path, dict(params, **({"pageToken": token} if token else {})))
            yield from (i for i in data.get("items") or [] if isinstance(i, dict))
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token or token in seen:
                return
            seen.add(token)

    def channel(self, spec: str) -> Optional[dict]:
        items = self.get("/channels", dict(channel_query(spec), part="snippet,contentDetails")).get("items") or []
        if not items:
            return None
        item = items[0]
        uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        return {"id": item.get("id"), "title": (item.get("snippet") or {}).get("title") or spec, "uploads": uploads}

    def upload_ids(self, playlist: str) -> List[str]:
        ids = [(i.get("contentDetails") or {}).get("videoId")
               for i in self.pages("/playlistItems", {"part": "contentDetails", "playlistId": playlist, "maxResults": PAGE})]
        return [i for i in ids if i]

    def videos(self, ids: List[str]) -> List[dict]:
        out = []
        for at in range(0, len(ids), PAGE):
            out.extend(self.get("/videos", {"part": "snippet,statistics,contentDetails",
                                            "id": ",".join(ids[at:at + PAGE]), "maxResults": PAGE}).get("items") or [])
        return [v for v in out if isinstance(v, dict) and v.get("id")]

    def comments(self, video_id: str, limit: int) -> List[dict]:
        if limit <= 0:
            return []
        data = self.get("/commentThreads", {"part": "snippet,replies", "videoId": video_id, "order": "relevance",
                                            "maxResults": min(limit, 100), "textFormat": "plainText"})
        return [i for i in data.get("items") or [] if isinstance(i, dict)][:limit]


def _comment_line(snippet: dict, owner: str, reply: bool) -> str:
    text = clip(" ".join((snippet.get("textDisplay") or snippet.get("textOriginal") or "").split()), MAX_COMMENT_CHARS)
    if not text:
        return ""
    mine = ((snippet.get("authorChannelId") or {}).get("value") == owner)
    if reply:
        return f"  ↳ {'автор канала: ' if mine else ''}{text}"
    likes = snippet.get("likeCount") or 0
    return f"— {'автор канала: ' if mine else ''}{text}" + (f" (лайков: {rounded(likes)})" if likes else "")


def build_record(video: dict, channel: dict, threads: Optional[List[dict]]) -> Record:
    """threads=None — комментарии отключены."""
    sn, st = video.get("snippet") or {}, video.get("statistics") or {}
    published = to_iso(sn.get("publishedAt")) or "1970-01-01T00:00:00Z"
    length = duration_text((video.get("contentDetails") or {}).get("duration"))
    stats = [f"{rounded(st.get('viewCount'))} просмотров"] if st.get("viewCount") is not None else []
    if st.get("likeCount") is not None:
        stats.append(f"{rounded(st['likeCount'])} лайков")
    if st.get("commentCount") is not None:
        stats.append(f"{rounded(st['commentCount'])} комментариев")
    parts = [f"Канал: {channel['title']}",
             "Опубликовано: " + " · ".join(x for x in (published[:10], length) if x)]
    if stats:
        parts.append("Статистика: " + " · ".join(stats))
    description = clip((sn.get("description") or "").strip(), MAX_DESCRIPTION_CHARS)
    if description:
        parts.append("\nОписание:\n" + description)
    if threads is None:
        parts.append("\nКомментарии под видео отключены.")
    else:
        lines = []
        for thread in threads:
            ts = thread.get("snippet") or {}
            top = _comment_line((ts.get("topLevelComment") or {}).get("snippet") or {}, channel["id"], reply=False)
            if not top:
                continue
            lines.append(top)
            for reply in ((thread.get("replies") or {}).get("comments") or []):
                line = _comment_line(reply.get("snippet") or {}, channel["id"], reply=True)
                if line:
                    lines.append(line)
        if lines:
            parts.append("\nКомментарии зрителей (сначала самые заметные):\n" + "\n".join(lines))
    return Record(source="youtube", external_id=video["id"], title=(sn.get("title") or video["id"]).strip(),
                  body="\n".join(parts), updated_at=published, started_at=published,
                  location=f"https://www.youtube.com/watch?v={video['id']}", kind="discussion")


class YouTubeConnector(ApiConnector):
    name = source = "youtube"
    title = "YouTube"
    about = "видео его YouTube-каналов со статистикой и комментариями зрителей"
    default_every_minutes = 360
    empty_hint = "Подключи ключ YouTube Data API и укажи каналы (README, раздел «YouTube»)."
    now: Optional[datetime] = None  # тесты фиксируют «сейчас»

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        channels = [c for c in self.settings.get("channels") or [] if str(c).strip()]
        if not channels:
            raise RuntimeError("YouTube: не указаны каналы — добавь sources.youtube.channels в config.json")
        service = self.settings.get("keychain_service") or "brain-youtube"
        account = self.settings.get("keychain_account") or "default"
        try:
            key = keychain.password(service, account, self.settings.get("key_command"))
        except RuntimeError:
            raise RuntimeError("YouTube: нет ключа API; создай его в Google Cloud и сохрани командой `"
                               + keychain.add_command(service, account) + "`") from None
        api = YouTubeAPI(self.settings.get("api_base") or API_BASE, key)
        try:
            pos = json.loads(cursor) if cursor else {}
        except ValueError:
            pos = {}
        if not isinstance(pos, dict) or not isinstance(pos.get("videos", {}), dict):
            pos = {}
        done = pos.setdefault("videos", {})
        now = self.now or datetime.now(timezone.utc)
        problems: List[str] = []
        queue: List[Tuple[dict, dict]] = []
        for spec in channels:
            channel = api.channel(str(spec))
            if not channel or not channel.get("uploads"):
                problems.append(f"YouTube: канал «{spec}» не найден")
                continue
            for video in api.videos(api.upload_ids(channel["uploads"])):
                published = parse((video.get("snippet") or {}).get("publishedAt"))
                if due(published, parse(done.get(video["id"])), now):
                    queue.append((video, channel))
        # Сначала видео, которых в базе ещё нет, внутри — от новых к старым.
        queue.sort(key=lambda item: (item[0]["id"] in done,
                                     -(parse((item[0].get("snippet") or {}).get("publishedAt")) or now).timestamp()))
        limit = int(self.settings.get("max_videos_per_run", 100))
        per_video = int(self.settings.get("comments_per_video", 30))
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        batch: List[Record] = []
        for video, channel in queue[:max(limit, 0)]:
            try:
                threads: Optional[List[dict]] = api.comments(video["id"], per_video)
            except CommentsDisabled:
                threads = None
            except Fatal:
                # Квота или связь: уже собранное сохраняем, остальное — в следующий прогон.
                if batch:
                    yield Batch(batch, json.dumps(pos, sort_keys=True))
                raise
            batch.append(build_record(video, channel, threads))
            done[video["id"]] = stamp
            if len(batch) >= BATCH:
                yield Batch(batch, json.dumps(pos, sort_keys=True))
                batch = []
        yield Batch(batch, json.dumps(pos, sort_keys=True))
        if problems:
            raise RuntimeError("; ".join(problems))

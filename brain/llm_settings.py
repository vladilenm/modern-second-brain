"""Настройки модели для CLI и BrainBar. Секреты никогда не входят в ответ интерфейсу."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import config

PROVIDERS = (
    ("SimpleClaw", "https://simple-claw.ru/v1", True),
    ("OpenRouter", "https://openrouter.ai/api/v1", True),
    ("OpenAI", "https://api.openai.com/v1", True),
    ("Ollama", "http://localhost:11434/v1", False),
)
PROFILE_FIELDS = ("model", "api_key", "json_mode", "use_env_key")


def endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Укажи адрес API.")
    if any(c.isspace() or ord(c) < 32 for c in value.strip()):
        raise ValueError("Адрес API не должен содержать пробелы или переносы.")
    try:
        parts = urllib.parse.urlsplit(value.strip())
        port = parts.port
    except ValueError:
        raise ValueError("Некорректный адрес API.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Адрес API должен начинаться с http:// или https://.")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("Адрес API не должен содержать пароль, параметры или фрагмент. Ключ вводится отдельно.")
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    netloc = host + (":" + str(port) if port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def provider_name(url: str) -> str:
    try:
        url = endpoint(url)
    except ValueError:
        return "Не настроен"
    for name, base, _ in PROVIDERS:
        if url.rstrip("/") == base:
            return name
    return urllib.parse.urlsplit(url).hostname or "Не настроен"


def processor(llm: dict) -> dict:
    """Безопасный снимок модели текущего прогона, без ключей и URL с параметрами."""
    return {"provider": provider_name(llm.get("base_url", "")), "model": llm.get("model", "")}


def read_raw() -> dict:
    path = config.config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("llm", {}), dict):
        raise ValueError("В config.json ожидается объект настроек с секцией llm.")
    profiles = data.get("llm", {}).get("profiles", {})
    if not isinstance(profiles, dict) or any(not isinstance(p, dict) for p in profiles.values()):
        raise ValueError("В llm.profiles ожидаются настройки провайдеров.")
    return data


def profile(raw: dict, url: str) -> dict:
    llm = raw.get("llm", {})
    stored = copy.deepcopy(llm.get("profiles", {}).get(url, {}))
    active_url = endpoint(llm["base_url"]) if llm.get("base_url") else ""
    if active_url == url:
        stored.update({key: llm[key] for key in PROFILE_FIELDS if key in llm})
        stored.setdefault("use_env_key", True)
    else:
        stored.setdefault("use_env_key", False)
    return stored


def api_key(settings: dict) -> str:
    if settings.get("use_env_key") and os.environ.get("BRAIN_LLM_API_KEY"):
        return os.environ["BRAIN_LLM_API_KEY"]
    return settings.get("api_key", "")


def describe(raw: dict = None) -> dict:
    raw = read_raw() if raw is None else raw
    llm = raw.get("llm", {})
    current_url = endpoint(llm["base_url"]) if llm.get("base_url") else ""
    urls = [base for _, base, _ in PROVIDERS]
    for url in [current_url] + list(llm.get("profiles", {})):
        if url and url not in urls:
            urls.append(url)
    providers = []
    for url in urls:
        saved = profile(raw, url)
        providers.append({
            "name": provider_name(url), "base_url": url, "model": saved.get("model", ""),
            "json_mode": saved.get("json_mode", True), "has_api_key": bool(api_key(saved)),
            "key_from_env": bool(saved.get("use_env_key") and os.environ.get("BRAIN_LLM_API_KEY")),
            "custom": url not in [base for _, base, _ in PROVIDERS],
            "requires_key": any(url == base and required for _, base, required in PROVIDERS),
        })
    return {"current": {**processor(llm), "base_url": current_url}, "providers": providers}


def candidate(payload: dict, raw: dict) -> dict:
    url = endpoint(payload.get("base_url", ""))
    result = profile(raw, url)
    result["base_url"] = url
    for key in ("model", "api_key"):
        if key in payload:
            if not isinstance(payload[key], str) or any(c in payload[key] for c in ("\n", "\r", "\0")):
                raise ValueError("Модель и API-ключ должны быть строками без переносов.")
            result[key] = payload[key].strip()
    if "api_key" in payload:
        result["use_env_key"] = False  # Явно введённый ключ заменяет переменную окружения.
    if "json_mode" in payload:
        if not isinstance(payload["json_mode"], bool):
            raise ValueError("json_mode должен быть true или false.")
        result["json_mode"] = payload["json_mode"]
    result.setdefault("json_mode", True)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Не переносить Authorization на другой адрес, даже если сервер вернул редирект.
        return None


def models(payload: dict) -> dict:
    settings = candidate(payload, read_raw())
    headers = {"Accept": "application/json"}
    secret = api_key(settings)
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = urllib.request.Request(settings["base_url"] + "/models", headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            content = response.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("Список моделей слишком большой.")
        data = json.loads(content)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        hint = "Проверь API-ключ." if code in (401, 403) else "Можно ввести название модели вручную."
        raise RuntimeError(f"Провайдер вернул HTTP {code}. {hint}") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Не удалось соединиться с API. Проверь адрес и подключение.") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("API вернул некорректный список моделей. Можно ввести модель вручную.") from None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError("API не поддерживает список моделей. Введи название вручную.")
    ids = set()
    for item in data["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        architecture = item.get("architecture")
        outputs = architecture.get("output_modalities", []) if isinstance(architecture, dict) else []
        if isinstance(outputs, list) and outputs and "text" not in outputs:
            continue
        if item["id"].strip():
            ids.add(item["id"].strip())
    return {"models": sorted(ids, key=str.casefold)}


def save(payload: dict) -> dict:
    raw = read_raw()  # Сохранить все прочие настройки, включая правки с момента открытия окна.
    chosen = candidate(payload, raw)
    if not chosen.get("model"):
        raise ValueError("Выбери модель из списка или введи её название.")
    if any(chosen["base_url"] == base and required for _, base, required in PROVIDERS) and not api_key(chosen):
        raise ValueError("Для этого провайдера нужен API-ключ.")
    llm = raw.setdefault("llm", {})
    profiles = llm.setdefault("profiles", {})
    old_url = endpoint(llm["base_url"]) if llm.get("base_url") else ""
    if old_url:
        profiles[old_url] = profile(raw, old_url)
    profiles[chosen["base_url"]] = {key: chosen[key] for key in PROFILE_FIELDS if key in chosen}
    llm.update({key: chosen.get(key, "" if key == "api_key" else None) for key in PROFILE_FIELDS})
    llm["base_url"] = chosen["base_url"]
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as out:
            temporary = out.name
            os.fchmod(out.fileno(), 0o600)
            json.dump(raw, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return describe(raw)

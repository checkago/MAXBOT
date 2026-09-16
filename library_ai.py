import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from library_service import LibraryService
from ssl_util import ssl_context

DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


class LibraryAssistant:
    def __init__(
        self,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_url = api_url.strip()
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _chat(self, system_prompt: str, user_prompt: str, max_tokens: int = 900, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds, context=ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek network error: {exc}") from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("DeepSeek returned empty response")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("DeepSeek returned invalid response")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("DeepSeek returned empty answer")
        return content.strip()

    @staticmethod
    def _parse_json(text: str) -> Any:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return json.loads(cleaned)

    def answer(self, libraries: LibraryService, question: str) -> str:
        catalog = libraries.catalog_for_ai()
        system_prompt = (
            "Ты помощник библиотечной системы г.о. Балашиха (МБУК ЦБС им. А. Белого).\n"
            "Отвечай только на основе переданных данных о библиотеках.\n"
            "Не выдумывай адреса, телефоны и часы работы.\n"
            "Если пользователь спрашивает про несколько библиотек — перечисли подходящие.\n"
            "Если данных недостаточно — честно скажи об этом.\n"
            "Отвечай по-русски, кратко и понятно, без markdown."
        )
        user_prompt = (
            f"Данные библиотек:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"Вопрос пользователя: {question.strip()}"
        )
        return self._chat(system_prompt, user_prompt, max_tokens=900)

    def expand_book_query(self, question: str) -> list[str]:
        system_prompt = (
            "Ты помощник поиска по библиотечному каталогу.\n"
            "Читатель может не помнить название и автора: он описывает сюжет, тему, жанр или ключевые детали.\n"
            "Верни только JSON без markdown: {\"terms\": [\"...\"]}.\n"
            "terms — 6-14 коротких русских слов и словосочетаний для поиска по названию, жанру и аннотации.\n"
            "Добавь синонимы и смежные темы (например: программирование -> python, информатика, код, алгоритмы).\n"
            "Не включай служебные слова вроде книга, найти, есть, каталог."
        )
        user_prompt = f"Запрос читателя: {question.strip()}"
        raw = self._chat(system_prompt, user_prompt, max_tokens=300, temperature=0.1)
        parsed = self._parse_json(raw)
        terms = parsed.get("terms") if isinstance(parsed, dict) else None
        if not isinstance(terms, list):
            return []
        result: list[str] = []
        for item in terms:
            if isinstance(item, str) and item.strip():
                result.append(item.strip().lower())
        return result

    def rank_books(self, question: str, candidates: list[dict[str, Any]]) -> str:
        system_prompt = (
            "Ты библиотекарь ЦБС Балашихи.\n"
            "По запросу читателя выбери до 5 наиболее подходящих книг ТОЛЬКО из переданного списка.\n"
            "Не выдумывай книги, авторов, филиалы и факты, которых нет в данных.\n"
            "Если ничего не подходит — так и скажи.\n"
            "Отвечай по-русски, кратко, без markdown.\n"
            "Формат: 1-2 предложения почему эти книги, затем нумерованный список:\n"
            "название, автор, зачем подошла, где взять."
        )
        user_prompt = (
            f"Запрос читателя: {question.strip()}\n\n"
            f"Кандидаты из каталога:\n{json.dumps(candidates, ensure_ascii=False)}"
        )
        return self._chat(system_prompt, user_prompt, max_tokens=900, temperature=0.2)


def create_library_assistant() -> LibraryAssistant | None:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key and os.path.isfile("deepseek_token.txt"):
        lines = [line.strip() for line in open("deepseek_token.txt", encoding="utf-8") if line.strip()]
        if lines:
            api_key = lines[-1]
    if not api_key:
        return None
    api_url = os.getenv("DEEPSEEK_API_URL", DEFAULT_API_URL).strip()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()
    return LibraryAssistant(api_key=api_key, api_url=api_url, model=model)

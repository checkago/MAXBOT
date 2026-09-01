import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_API_URL = "https://obs-balashiha.ru/api/v1/biblioteka_list/"
CACHE_TTL_SECONDS = 3600


@dataclass
class Library:
    id: int
    name: str
    slug: str
    phone: str
    email: str
    address: str
    workdays: str
    worktime: str
    workdays_alt: str
    worktime_alt: str
    weekend: str


class LibraryService:
    def __init__(self, api_url: str = DEFAULT_API_URL, cache_ttl: int = CACHE_TTL_SECONDS) -> None:
        self.api_url = api_url
        self.cache_ttl = cache_ttl
        self._libraries: list[Library] | None = None
        self._loaded_at: float = 0.0

    def _fetch_raw(self) -> list[dict[str, Any]]:
        req = urllib.request.Request(self.api_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list):
            raise RuntimeError("Unexpected library API response")
        return data

    def _parse(self, item: dict[str, Any]) -> Library:
        return Library(
            id=int(item["id"]),
            name=str(item.get("name") or "").strip(),
            slug=str(item.get("slug") or "").strip(),
            phone=str(item.get("phone") or "").strip(),
            email=str(item.get("email") or "").strip(),
            address=str(item.get("adress") or item.get("address") or "").strip(),
            workdays=str(item.get("workdays") or "").strip(),
            worktime=str(item.get("worktime") or "").strip(),
            workdays_alt=str(item.get("workdays_alt") or "").strip(),
            worktime_alt=str(item.get("worktime_alt") or "").strip(),
            weekend=str(item.get("weekend") or "").strip(),
        )

    def get_all(self, force_refresh: bool = False) -> list[Library]:
        now = time.time()
        if not force_refresh and self._libraries is not None and now - self._loaded_at < self.cache_ttl:
            return self._libraries
        raw = self._fetch_raw()
        self._libraries = [self._parse(item) for item in raw]
        self._loaded_at = now
        return self._libraries

    def find_by_id(self, library_id: int) -> Library | None:
        for lib in self.get_all():
            if lib.id == library_id:
                return lib
        return None

    def search(self, query: str) -> list[Library]:
        query = query.strip().lower()
        if not query:
            return []
        if query.isdigit():
            lib = self.find_by_id(int(query))
            return [lib] if lib else []
        libraries = self.get_all()
        exact_slug = [lib for lib in libraries if lib.slug.lower() == query]
        if exact_slug:
            return exact_slug
        substring = [
            lib
            for lib in libraries
            if query in lib.name.lower()
            or query in lib.slug.lower()
            or query in lib.address.lower()
        ]
        if substring:
            return substring
        words = [word for word in re.split(r"\s+", query) if len(word) >= 2]
        if not words:
            return []
        return [
            lib
            for lib in libraries
            if all(word in f"{lib.name} {lib.slug} {lib.address}".lower() for word in words)
        ]

    def search_natural(self, text: str) -> list[Library]:
        query = self._extract_search_terms(text)
        if not query:
            return []
        results = self.search(query)
        if results:
            return results
        words = [word for word in re.split(r"\s+", query) if len(word) >= 3]
        for word in words:
            results = self.search(word)
            if results:
                return results
        return []

    @staticmethod
    def _extract_search_terms(text: str) -> str:
        cleaned = re.sub(
            r"(?i)\b("
            r"библиотек[аиуеы]?|контакт[ыа]?|расписани[ея]|режим|работ[ыа]|работает|"
            r"телефон|адрес|найди|покажи|где|какая|какой|когда|как|что|сколько|"
            r"есть|ли|в|на|у|о|об|про|это|эта|этот|скажи|подскажи|интересует"
            r")\b",
            " ",
            text,
        )
        cleaned = re.sub(r"[?!.,]", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def format_list(self) -> str:
        lines = ["Библиотеки ЦБС Балашихи:", ""]
        for lib in self.get_all():
            lines.append(f"{lib.id}. {lib.name}")
        lines.extend(["", "Или спросите: «когда работает библиотека в Павлино?»"])
        return "\n".join(lines)

    def format_library(self, lib: Library) -> str:
        lines = [lib.name, ""]
        if lib.address:
            lines.append(f"Адрес: {lib.address}")
        if lib.phone:
            lines.append(f"Телефон: {lib.phone}")
        if lib.email:
            lines.append(f"Email: {lib.email}")

        schedule: list[str] = []
        if lib.workdays and lib.worktime:
            schedule.append(f"{lib.workdays}: {lib.worktime}")
        if lib.workdays_alt and lib.worktime_alt:
            schedule.append(f"{lib.workdays_alt}: {lib.worktime_alt}")
        if lib.weekend:
            schedule.append(f"Выходной: {lib.weekend}")
        if schedule:
            lines.extend(["", "Режим работы:"])
            lines.extend(f"  {part}" for part in schedule)
        return "\n".join(lines)

    def catalog_for_ai(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for lib in self.get_all():
            schedule: list[str] = []
            if lib.workdays and lib.worktime:
                schedule.append(f"{lib.workdays}: {lib.worktime}")
            if lib.workdays_alt and lib.worktime_alt:
                schedule.append(f"{lib.workdays_alt}: {lib.worktime_alt}")
            if lib.weekend:
                schedule.append(f"Выходной: {lib.weekend}")
            address = re.sub(r",\s*,", ",", lib.address).strip(" ,")
            items.append(
                {
                    "id": lib.id,
                    "name": lib.name,
                    "address": address,
                    "phone": lib.phone,
                    "email": lib.email,
                    "schedule": schedule,
                }
            )
        return items

    def format_natural_result(self, text: str) -> str | None:
        try:
            results = self.search_natural(text)
        except Exception:
            return None
        if not results:
            return None
        if len(results) == 1:
            return self.format_library(results[0])
        lines = [f"Найдено библиотек: {len(results)}", ""]
        for lib in results[:10]:
            lines.append(f"{lib.id}. {lib.name}")
        lines.extend(["", "Уточните запрос, например: «библиотека 4»"])
        return "\n".join(lines)

    def format_search_result(self, query: str) -> str:
        try:
            results = self.search(query)
        except Exception as exc:
            return f"Не удалось загрузить данные библиотек: {exc}"
        if not results:
            return f"Библиотека «{query}» не найдена. Список: /biblioteki"
        if len(results) == 1:
            return self.format_library(results[0])
        lines = [f"Найдено библиотек: {len(results)}", ""]
        for lib in results[:10]:
            lines.append(f"{lib.id}. {lib.name}")
        lines.extend(["", "Подробнее: напишите «библиотека <номер>»"])
        return "\n".join(lines)

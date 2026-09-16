import json
import os
import re
from typing import Any, Protocol


DEFAULT_CATALOG_PATH = "books_balashiha.json"
MAX_RESULTS = 5
CANDIDATE_LIMIT = 20
STOPWORDS = {
    "а", "в", "во", "и", "или", "на", "по", "про", "для", "из", "к", "ко", "о", "об",
    "от", "с", "со", "у", "это", "этот", "эта", "эти", "что", "как", "какие", "какой",
    "какая", "есть", "хочу", "хотел", "хотела", "нужно", "нужна", "найди", "найти",
    "ищу", "подбери", "книга", "книгу", "книги", "книг", "каталог", "каталогах",
    "наших", "наш", "нашем", "пожалуйста", "мне", "мы", "вас", "ваш", "ваши",
}


class BookQueryAssistant(Protocol):
    def expand_book_query(self, question: str) -> list[str]:
        ...

    def rank_books(self, question: str, candidates: list[dict[str, Any]]) -> str:
        ...


class BookCatalogService:
    def __init__(self, catalog_path: str = DEFAULT_CATALOG_PATH) -> None:
        self.catalog_path = catalog_path
        self._books: list[dict[str, Any]] | None = None
        self._index: list[dict[str, Any]] | None = None
        self._load_error: str | None = None

    def is_available(self) -> bool:
        self._ensure_loaded()
        return self._books is not None

    def book_count(self) -> int:
        self._ensure_loaded()
        return len(self._books) if self._books is not None else 0

    def _ensure_loaded(self) -> None:
        if self._books is not None or self._load_error is not None:
            return
        if not os.path.isfile(self.catalog_path):
            self._load_error = f"Файл каталога не найден: {self.catalog_path}"
            return
        try:
            with open(self.catalog_path, encoding="utf-8") as fh:
                data = json.load(fh)
            books = data.get("books") if isinstance(data, dict) else data
            if not isinstance(books, list):
                self._load_error = "Неверный формат каталога книг"
                return
            self._books = books
            self._index = [self._index_book(book) for book in books]
        except (OSError, json.JSONDecodeError) as exc:
            self._load_error = f"Не удалось загрузить каталог: {exc}"

    @staticmethod
    def _index_book(book: dict[str, Any]) -> dict[str, Any]:
        title = str(book.get("title") or "")
        author = str(book.get("author") or "")
        genre = str(book.get("genre") or "")
        annotation = str(book.get("annotation") or "")
        description = str(book.get("description") or "")
        return {
            "book": book,
            "title": title.lower(),
            "author": author.lower(),
            "genre": genre.lower(),
            "annotation": annotation.lower(),
            "description": description.lower(),
        }

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        words = [word for word in re.split(r"[^\wё+-]+", text.lower(), flags=re.I) if word]
        return [word for word in words if len(word) >= 3 and word not in STOPWORDS]

    def search(self, query: str, extra_terms: list[str] | None = None, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if self._index is None:
            return []

        terms = self._tokenize(query)
        if extra_terms:
            for term in extra_terms:
                terms.extend(self._tokenize(term))
        unique_terms = list(dict.fromkeys(terms))
        if not unique_terms:
            unique_terms = [query.strip().lower()] if query.strip() else []
        if not unique_terms:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self._index:
            score = 0
            hits = 0
            for term in unique_terms:
                if term in item["title"]:
                    score += 12
                    hits += 1
                elif term in item["author"]:
                    score += 10
                    hits += 1
                elif term in item["genre"]:
                    score += 8
                    hits += 1
                elif term in item["annotation"]:
                    score += 5
                    hits += 1
                elif term in item["description"]:
                    score += 3
                    hits += 1
            if hits == 0:
                continue
            if len(unique_terms) >= 3 and hits == 1 and score < 8:
                continue
            scored.append((score, item["book"]))

        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or "")))
        return [book for _, book in scored[:limit]]

    def _format_branches(self, book: dict[str, Any]) -> str:
        branches = book.get("branches") or []
        if not isinstance(branches, list) or not branches:
            return "филиалы не указаны"
        names: list[str] = []
        for branch in branches[:4]:
            if not isinstance(branch, dict):
                continue
            name = str(branch.get("name") or "").strip()
            if name:
                names.append(name)
        if not names:
            return "филиалы не указаны"
        suffix = ""
        if len(branches) > len(names):
            suffix = f" (+{len(branches) - len(names)})"
        return ", ".join(names) + suffix

    def format_book(self, book: dict[str, Any]) -> str:
        title = str(book.get("title") or "Без названия").strip()
        author = str(book.get("author") or "Автор не указан").strip()
        lines = [title, f"Автор: {author}"]
        genre = str(book.get("genre") or "").strip()
        if genre:
            lines.append(f"Жанр: {genre}")
        year = str(book.get("yearOfPublishing") or "").strip()
        if year:
            lines.append(f"Год: {year}")
        annotation = re.sub(r"\s+", " ", str(book.get("annotation") or "")).strip()
        if annotation:
            if len(annotation) > 280:
                annotation = annotation[:277].rstrip() + "..."
            lines.append(annotation)
        lines.append(f"Есть в: {self._format_branches(book)}")
        book_id = str(book.get("bookCardId") or "").strip()
        if book_id:
            lines.append(f"Каталог: https://biblio.mosreg.ru/books/{book_id}")
        return "\n".join(lines)

    def _candidate(self, book: dict[str, Any]) -> dict[str, Any]:
        annotation = re.sub(r"\s+", " ", str(book.get("annotation") or "")).strip()
        if len(annotation) > 400:
            annotation = annotation[:397].rstrip() + "..."
        return {
            "title": str(book.get("title") or "").strip(),
            "author": str(book.get("author") or "").strip(),
            "genre": str(book.get("genre") or "").strip(),
            "year": str(book.get("yearOfPublishing") or "").strip(),
            "annotation": annotation,
            "branches": self._format_branches(book),
        }

    def _format_local_results(self, query: str, results: list[dict[str, Any]]) -> str:
        if len(results) == 1:
            return self.format_book(results[0])
        lines = [f"Найдено книг по запросу «{query}»:", ""]
        for index, book in enumerate(results, start=1):
            title = str(book.get("title") or "Без названия").strip()
            author = str(book.get("author") or "Автор не указан").strip()
            genre = str(book.get("genre") or "").strip()
            lines.append(f"{index}. {title}")
            lines.append(f"   {author}")
            if genre:
                lines.append(f"   {genre}")
            lines.append(f"   {self._format_branches(book)}")
            lines.append("")
        return "\n".join(lines).strip()

    def format_search_result(self, query: str, assistant: BookQueryAssistant | None = None) -> str:
        self._ensure_loaded()
        if self._load_error:
            return (
                f"{self._load_error}\n"
                "Пока используйте каталог: https://biblio.mosreg.ru/books"
            )

        extra_terms: list[str] = []
        if assistant is not None:
            try:
                extra_terms = assistant.expand_book_query(query)
            except Exception:
                extra_terms = []

        results = self.search(query, extra_terms=extra_terms, limit=CANDIDATE_LIMIT)
        if not results:
            return (
                f"Книги по запросу «{query}» не найдены.\n"
                "Опишите тему, сюжет или ключевые слова иначе."
            )

        if assistant is not None:
            try:
                return assistant.rank_books(query, [self._candidate(book) for book in results[:CANDIDATE_LIMIT]])
            except Exception:
                pass

        return self._format_local_results(query, results[:MAX_RESULTS])


def create_book_service() -> BookCatalogService:
    catalog_path = os.getenv("BOOKS_CATALOG_PATH", DEFAULT_CATALOG_PATH).strip()
    return BookCatalogService(catalog_path=catalog_path)

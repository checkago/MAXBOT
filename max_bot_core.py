import json
import http.server
import os
import re
import socketserver
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from library_ai import LibraryAssistant, create_library_assistant
from library_service import LibraryService


@dataclass
class ChatMessage:
    message_id: str
    chat_id: str
    user_id: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    reply_user_id: str = ""
    reply_chat_id: str = ""
    callback_payload: str = ""


@dataclass
class BotReply:
    text: str
    attachments: list[dict[str, Any]] | None = None


@dataclass
class BotContext:
    message: ChatMessage
    session: dict[str, Any]


class SessionStorage:
    def get(self, user_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def set(self, user_id: str, value: dict[str, Any]) -> None:
        raise NotImplementedError


class InMemorySessionStorage(SessionStorage):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, user_id: str) -> dict[str, Any]:
        return self._data.setdefault(user_id, {})

    def set(self, user_id: str, value: dict[str, Any]) -> None:
        self._data[user_id] = value


class MaxApiClient:
    """
    Lightweight HTTP client for MAX bot API.

    API contracts differ between installations, so endpoint paths and field names
    are configurable through environment variables.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        updates_path: str = "/updates",
        send_path: str = "/messages",
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.updates_path = updates_path
        self.send_path = send_path
        self.timeout_seconds = timeout_seconds

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url=url,
            data=body,
            method=method.upper(),
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {}
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"result": parsed}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} for {url}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error for {url}: {exc}") from exc

    def _request_get(self, path: str, query: dict[str, Any], timeout_seconds: int | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            encoded = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
            if encoded:
                url = f"{url}?{encoded}"
        req = urllib.request.Request(
            url=url,
            method="GET",
            headers={
                "Authorization": self.token,
                "Accept": "application/json",
            },
        )
        read_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            with urllib.request.urlopen(req, timeout=read_timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {}
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"result": parsed}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} for {url}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error for {url}: {exc}") from exc

    def get_updates(
        self, marker: int | None = None, limit: int = 100, timeout: int = 30
    ) -> tuple[list[dict[str, Any]], int | None]:
        query: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "types": "message_created,bot_started,message_callback",
        }
        if marker is not None:
            query["marker"] = marker
        data = self._request_get(self.updates_path, query, timeout_seconds=timeout + 15)
        next_marker: int | None = None
        updates: list[dict[str, Any]] = []
        if isinstance(data, list):
            updates = data
        elif isinstance(data, dict):
            marker_val = data.get("marker")
            if isinstance(marker_val, (int, float)):
                next_marker = int(marker_val)
            for key in ("updates", "items", "result", "data"):
                if isinstance(data.get(key), list):
                    updates = data[key]
                    break
        return updates, next_marker

    def send_message(
        self,
        text: str,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        if user_id:
            path = f"{self.send_path}?{urllib.parse.urlencode({'user_id': int(user_id)})}"
        elif chat_id:
            path = f"{self.send_path}?{urllib.parse.urlencode({'chat_id': int(chat_id)})}"
        else:
            raise ValueError("user_id or chat_id is required to send a message")
        payload: dict[str, Any] = {"text": text}
        if attachments:
            payload["attachments"] = attachments
        self._request_json("POST", path, payload)

    def send_text(self, text: str, *, user_id: str | None = None, chat_id: str | None = None) -> None:
        self.send_message(text, user_id=user_id, chat_id=chat_id)

    def set_webhook_subscription(self, webhook_url: str, secret: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": webhook_url}
        if secret:
            payload["secret"] = secret
        return self._request_json("POST", "/subscriptions", payload)


class MaxUpdateParser:
    """
    Adapter from raw update JSON to normalized ChatMessage.

    Default mapping is intentionally tolerant and checks multiple common field names.
    """

    @staticmethod
    def parse(update: dict[str, Any]) -> ChatMessage | None:
        update_type = MaxUpdateParser._pick_str(update, ["update_type", "type"]) or ""
        if update_type == "bot_started":
            user = update.get("user") if isinstance(update.get("user"), dict) else {}
            chat_id = MaxUpdateParser._pick_str(update, ["chat_id"]) or ""
            user_id = MaxUpdateParser._pick_str(user, ["user_id", "id"]) or ""
            if not user_id:
                return None
            payload = MaxUpdateParser._pick_str(update, ["payload"]) or ""
            text = f"/start {payload}".strip()
            message_id = MaxUpdateParser._pick_str(update, ["timestamp", "update_id", "id"]) or ""
            return ChatMessage(
                message_id=message_id,
                chat_id=chat_id or user_id,
                user_id=user_id,
                text=text,
                raw=update,
                reply_user_id=user_id,
                reply_chat_id=chat_id,
            )

        if update_type == "message_callback":
            callback = update.get("callback") if isinstance(update.get("callback"), dict) else {}
            payload = callback.get("payload")
            if not isinstance(payload, str) or not payload.strip():
                return None
            user = callback.get("user") if isinstance(callback.get("user"), dict) else {}
            if not user and isinstance(update.get("user"), dict):
                user = update["user"]
            user_id = MaxUpdateParser._pick_str(user, ["user_id", "id"]) or ""
            chat_id = MaxUpdateParser._pick_str(update, ["chat_id"]) or ""
            message_id = (
                MaxUpdateParser._pick_str(callback, ["callback_id", "id"])
                or MaxUpdateParser._pick_str(update, ["timestamp"])
                or ""
            )
            if not user_id:
                return None
            return ChatMessage(
                message_id=message_id,
                chat_id=chat_id or user_id,
                user_id=user_id,
                text="",
                raw=update,
                reply_user_id=user_id,
                reply_chat_id=chat_id,
                callback_payload=payload.strip(),
            )

        if update_type == "message_created":
            message = update.get("message")
            if not isinstance(message, dict):
                return None
            sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
            if sender.get("is_bot") is True:
                return None
            body = message.get("body") if isinstance(message.get("body"), dict) else {}
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            user_id = MaxUpdateParser._pick_str(sender, ["user_id", "id"]) or ""
            recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
            chat_id = (
                MaxUpdateParser._pick_str(recipient, ["chat_id"])
                or MaxUpdateParser._pick_str(update, ["chat_id"])
                or user_id
            )
            if not user_id:
                return None
            message_id = (
                MaxUpdateParser._pick_str(body, ["mid", "message_id", "id"])
                or MaxUpdateParser._pick_str(message, ["timestamp"])
                or MaxUpdateParser._pick_str(update, ["timestamp"])
                or ""
            )
            return ChatMessage(
                message_id=message_id,
                chat_id=chat_id,
                user_id=user_id,
                text=text.strip(),
                raw=update,
                reply_user_id=user_id,
                reply_chat_id=chat_id,
            )

        message = update.get("message") if isinstance(update.get("message"), dict) else update
        if not isinstance(message, dict):
            return None

        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        text = MaxUpdateParser._pick_str(body, ["text"]) or MaxUpdateParser._pick_str(message, ["text", "message"])
        if text is None:
            return None

        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        if sender.get("is_bot") is True:
            return None

        message_id = (
            MaxUpdateParser._pick_str(body, ["mid", "message_id", "id"])
            or MaxUpdateParser._pick_str(message, ["message_id", "id"])
            or MaxUpdateParser._pick_str(update, ["timestamp", "update_id"])
            or ""
        )
        chat_id = (
            MaxUpdateParser._pick_nested_str(message, ["recipient", "chat_id"])
            or MaxUpdateParser._pick_nested_str(message, ["chat", "id"])
            or MaxUpdateParser._pick_str(message, ["chat_id", "conversation_id"])
            or MaxUpdateParser._pick_str(update, ["chat_id"])
            or ""
        )
        user_id = (
            MaxUpdateParser._pick_nested_str(message, ["sender", "user_id"])
            or MaxUpdateParser._pick_nested_str(message, ["sender", "id"])
            or MaxUpdateParser._pick_nested_str(message, ["from", "id"])
            or MaxUpdateParser._pick_nested_str(message, ["user", "id"])
            or MaxUpdateParser._pick_str(message, ["user_id", "sender_id"])
            or MaxUpdateParser._pick_nested_str(update, ["user", "user_id"])
            or MaxUpdateParser._pick_nested_str(update, ["user", "id"])
            or ""
        )
        if not user_id and not chat_id:
            return None

        return ChatMessage(
            message_id=message_id,
            chat_id=chat_id or user_id,
            user_id=user_id or chat_id,
            text=text.strip(),
            raw=update,
            reply_user_id=user_id,
            reply_chat_id=chat_id,
        )

    @staticmethod
    def update_key(update: dict[str, Any]) -> str:
        update_type = str(update.get("update_type") or update.get("type") or "")
        timestamp = update.get("timestamp")
        if update_type == "message_callback":
            callback = update.get("callback") if isinstance(update.get("callback"), dict) else {}
            user = callback.get("user") if isinstance(callback.get("user"), dict) else {}
            if not user and isinstance(update.get("user"), dict):
                user = update["user"]
            callback_id = callback.get("callback_id") or callback.get("id") or ""
            payload = callback.get("payload") or ""
            user_id = user.get("user_id") or ""
            return f"{update_type}:{timestamp}:{callback_id}:{payload}:{user_id}"

        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        mid = body.get("mid") or body.get("id") or ""
        user_id = sender.get("user_id") or ""
        text = body.get("text") if isinstance(body.get("text"), str) else ""
        return f"{update_type}:{timestamp}:{mid}:{user_id}:{text}"

    @staticmethod
    def _pick_str(obj: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float)):
                return str(value)
        return None

    @staticmethod
    def _pick_nested_str(obj: dict[str, Any], path: list[str]) -> str | None:
        cur: Any = obj
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        if isinstance(cur, str):
            return cur
        if isinstance(cur, (int, float)):
            return str(cur)
        return None


Handler = Callable[[BotContext], str | BotReply | None]


CALLBACK_ACTIONS = {
    "menu:search": "action:search_books",
    "menu:libraries": "/biblioteki",
    "menu:help": "/help",
}

BUTTON_LABELS = {
    "поиск книг": "action:search_books",
    "библиотеки": "/biblioteki",
    "помощь": "/help",
}


def main_menu_attachments() -> list[dict[str, Any]]:
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {"type": "callback", "text": "Поиск книг", "payload": "menu:search"},
                        {"type": "callback", "text": "Библиотеки", "payload": "menu:libraries"},
                        {"type": "callback", "text": "Помощь", "payload": "menu:help"},
                    ]
                ]
            },
        }
    ]


def as_menu_reply(text: str, with_menu: bool = True) -> BotReply:
    return BotReply(text=text, attachments=main_menu_attachments() if with_menu else None)


def map_user_input(text: str) -> str:
    raw = text.strip()
    if not raw or raw.startswith("/") or raw.startswith("action:"):
        return raw

    lowered = raw.lower()
    exact_phrases = {
        **BUTTON_LABELS,
        "список библиотек": "/biblioteki",
        "все библиотеки": "/biblioteki",
        "справка": "/help",
        "найти книгу": "action:search_books",
        "поиск": "action:search_books",
        "начать": "/start",
        "старт": "/start",
        "привет": "/start",
    }
    if lowered in exact_phrases:
        return exact_phrases[lowered]

    library_match = re.match(r"^(?:библиотек[аиуеы]?|филиал)\s+(.+)$", raw, re.I)
    if library_match:
        return f"/biblioteka {library_match.group(1).strip()}"

    if re.search(r"\b(список|все)\s+библиотек", lowered):
        return "/biblioteki"

    return raw


def normalize_reply(reply: str | BotReply | None) -> BotReply | None:
    if reply is None:
        return None
    if isinstance(reply, BotReply):
        return reply
    return BotReply(text=reply)


class BotCore:
    def __init__(self, client: MaxApiClient, storage: SessionStorage | None = None) -> None:
        self.client = client
        self.storage = storage or InMemorySessionStorage()
        self.command_handlers: dict[str, Handler] = {}
        self.fallback_handler: Handler | None = None
        self._marker: int | None = None
        self._seen_updates: dict[str, float] = {}
        self._seen_updates_ttl = 3600.0

    def _remember_update(self, upd: dict[str, Any]) -> bool:
        key = MaxUpdateParser.update_key(upd)
        now = time.time()
        self._seen_updates = {
            seen_key: seen_at
            for seen_key, seen_at in self._seen_updates.items()
            if now - seen_at < self._seen_updates_ttl
        }
        if key in self._seen_updates:
            return False
        self._seen_updates[key] = now
        return True

    def _handle_update(self, upd: dict[str, Any]) -> bool:
        if not self._remember_update(upd):
            return False
        msg = MaxUpdateParser.parse(upd)
        if not msg:
            return False
        reply = self._dispatch(msg)
        if reply and reply.text:
            try:
                if msg.reply_user_id:
                    self.client.send_message(
                        reply.text,
                        user_id=msg.reply_user_id,
                        attachments=reply.attachments,
                    )
                elif msg.reply_chat_id:
                    self.client.send_message(
                        reply.text,
                        chat_id=msg.reply_chat_id,
                        attachments=reply.attachments,
                    )
                else:
                    self.client.send_message(
                        reply.text,
                        user_id=msg.user_id,
                        attachments=reply.attachments,
                    )
            except Exception:
                print("Failed to send reply:")
                traceback.print_exc()
                return False
        return True

    def command(self, name: str) -> Callable[[Handler], Handler]:
        name = name.strip().lower()

        def decorator(func: Handler) -> Handler:
            self.command_handlers[name] = func
            return func

        return decorator

    def on_fallback(self, func: Handler) -> Handler:
        self.fallback_handler = func
        return func

    def _dispatch(self, message: ChatMessage) -> BotReply | None:
        session = self.storage.get(message.user_id)
        ctx = BotContext(message=message, session=session)

        if message.callback_payload:
            text = CALLBACK_ACTIONS.get(message.callback_payload, message.callback_payload)
        else:
            text = message.text.strip()

        text = map_user_input(text)

        if text.startswith("/"):
            cmd = text.split()[0].lower()
            handler = self.command_handlers.get(cmd)
            if handler:
                reply = normalize_reply(handler(ctx))
                self.storage.set(message.user_id, ctx.session)
                return reply

        if text.startswith("action:"):
            action_reply = handle_action(text, ctx)
            self.storage.set(message.user_id, ctx.session)
            return action_reply

        if self.fallback_handler:
            reply = normalize_reply(self.fallback_handler(ctx))
            self.storage.set(message.user_id, ctx.session)
            return reply
        return None

    def poll_once(self) -> int:
        updates, next_marker = self.client.get_updates(marker=self._marker, limit=100, timeout=30)
        handled = 0
        for upd in updates:
            if self._handle_update(upd):
                handled += 1
        if next_marker is not None:
            self._marker = next_marker
        return handled

    def run_forever(self, idle_seconds: float = 1.0) -> None:
        backoff = 1.0
        while True:
            try:
                handled = self.poll_once()
                backoff = 1.0
                if handled == 0:
                    time.sleep(idle_seconds)
            except KeyboardInterrupt:
                raise
            except Exception:
                print("Polling error:")
                traceback.print_exc()
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    def run_webhook_server(self, host: str, port: int, webhook_path: str = "/webhook") -> None:
        bot = self
        expected_secret = os.getenv("MAX_WEBHOOK_SECRET", "").strip()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != webhook_path:
                    self.send_response(404)
                    self.end_headers()
                    return

                if expected_secret:
                    header_secret = self.headers.get("X-Webhook-Secret", "")
                    if header_secret != expected_secret:
                        self.send_response(401)
                        self.end_headers()
                        return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length).decode("utf-8")
                    data = json.loads(body) if body.strip() else {}
                    updates = data if isinstance(data, list) else [data]
                    for upd in updates:
                        if isinstance(upd, dict):
                            bot._handle_update(upd)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except Exception:
                    traceback.print_exc()
                    self.send_response(500)
                    self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        with socketserver.ThreadingTCPServer((host, port), Handler) as httpd:
            print(f"Webhook server listening on http://{host}:{port}{webhook_path}")
            httpd.serve_forever()


HELP_TEXT = (
    "Что можно написать:\n"
    "• библиотеки — список филиалов\n"
    "• библиотека 4 — контакты и режим работы\n"
    "• или просто спросите: «когда работает библиотека в Павлино?»\n"
    "• поиск книг — найти книгу\n"
    "• помощь — эта справка\n\n"
    "Или используйте кнопки меню ниже."
)


def handle_action(action: str, ctx: BotContext) -> BotReply:
    if action == "action:search_books":
        ctx.session["mode"] = "book_search"
        return as_menu_reply(
            "Введите название книги или автора.\n"
            "Пример: Толстой\n"
            "Пример: война и мир"
        )
    return as_menu_reply(f"Неизвестное действие: {action}")


def library_query_from_message(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def reply_to_text(
    libraries: LibraryService,
    text: str,
    session: dict[str, Any] | None = None,
    assistant: LibraryAssistant | None = None,
) -> BotReply:
    session = session or {}
    text = map_user_input(text.strip())
    if not text:
        return as_menu_reply(HELP_TEXT)

    if text.startswith("action:"):
        ctx = BotContext(message=ChatMessage("", "", "", text), session=session)
        return handle_action(text, ctx)

    if session.get("mode") == "book_search" and not text.startswith("/"):
        session["mode"] = ""
        return as_menu_reply(
            f"Поиск книг «{text}» скоро будет доступен.\n"
            "Пока используйте каталог: https://biblio.mosreg.ru/books"
        )

    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd == "/start":
            return as_menu_reply(
                "Привет! Я бот библиотечной системы Балашихи.\n"
                "Спросите по-человечески, например: «где детская библиотека?»\n"
                "Или нажмите кнопку ниже."
            )
        if cmd == "/help":
            return as_menu_reply(HELP_TEXT)
        if cmd == "/ping":
            return as_menu_reply("pong")
        if cmd == "/biblioteki":
            try:
                return as_menu_reply(libraries.format_list())
            except Exception as exc:
                return as_menu_reply(f"Не удалось загрузить список библиотек: {exc}")
        if cmd == "/biblioteka":
            query = library_query_from_message(text)
            if not query:
                return as_menu_reply(
                    "Укажите библиотеку, например: библиотека 4\n"
                    "Или нажмите «Библиотеки»."
                )
            return as_menu_reply(libraries.format_search_result(query))

    if assistant and assistant.is_available():
        try:
            return as_menu_reply(assistant.answer(libraries, text))
        except Exception as exc:
            return as_menu_reply(f"Не удалось получить ответ от ИИ: {exc}")

    lowered = text.lower()
    if re.search(r"(?i)(библиотек|контакт|расписан|режим|телефон|адрес|работает|график)", lowered):
        natural = libraries.format_natural_result(text)
        if natural:
            return as_menu_reply(natural)
        try:
            return as_menu_reply(libraries.format_list())
        except Exception as exc:
            return as_menu_reply(f"Не удалось загрузить список библиотек: {exc}")

    return as_menu_reply(f"Не понял запрос. Напишите «помощь» или нажмите кнопку.\nПолучил: {text}")


def create_library_service() -> LibraryService:
    library_api_url = os.getenv("LIBRARY_API_URL", "https://obs-balashiha.ru/api/v1/biblioteka_list/").strip()
    return LibraryService(api_url=library_api_url)


def run_cli_mode() -> None:
    libraries = create_library_service()
    assistant = create_library_assistant()
    if assistant:
        print("DeepSeek подключён — можно задавать вопросы на русском.")
    else:
        print("DeepSeek не настроен (нет DEEPSEEK_API_KEY или deepseek_token.txt).")
    print("CLI-режим. Выход: exit")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"exit", "quit", "q"}:
            break
        if not text:
            continue
        print(reply_to_text(libraries, text, assistant=assistant).text)
        print()


def build_bot_from_env() -> BotCore:
    base_url = os.getenv("MAX_API_BASE_URL", "").strip()
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    updates_path = os.getenv("MAX_UPDATES_PATH", "/bot/updates").strip()
    send_path = os.getenv("MAX_SEND_PATH", "/messages").strip()
    if updates_path == "/bot/updates":
        updates_path = "/updates"

    if not base_url:
        raise RuntimeError("MAX_API_BASE_URL is required")
    if not token:
        raise RuntimeError("MAX_BOT_TOKEN is required")

    client = MaxApiClient(
        base_url=base_url,
        token=token,
        updates_path=updates_path,
        send_path=send_path,
    )
    bot = BotCore(client=client)
    libraries = create_library_service()
    assistant = create_library_assistant()

    @bot.command("/start")
    def start(ctx: BotContext) -> BotReply:
        ctx.session.setdefault("visits", 0)
        ctx.session["visits"] += 1
        return reply_to_text(libraries, "/start", ctx.session, assistant)

    @bot.command("/help")
    def help_cmd(ctx: BotContext) -> BotReply:
        return reply_to_text(libraries, "/help", ctx.session, assistant)

    @bot.command("/biblioteki")
    def biblioteki(ctx: BotContext) -> BotReply:
        return reply_to_text(libraries, "/biblioteki", ctx.session, assistant)

    @bot.command("/biblioteka")
    def biblioteka(ctx: BotContext) -> BotReply:
        return reply_to_text(libraries, ctx.message.text, ctx.session, assistant)

    @bot.command("/ping")
    def ping(ctx: BotContext) -> BotReply:
        return reply_to_text(libraries, "/ping", ctx.session, assistant)

    @bot.on_fallback
    def fallback(ctx: BotContext) -> BotReply:
        if ctx.message.callback_payload:
            mapped = CALLBACK_ACTIONS.get(ctx.message.callback_payload, ctx.message.callback_payload)
            return reply_to_text(libraries, mapped, ctx.session, assistant)
        return reply_to_text(libraries, ctx.message.text, ctx.session, assistant)

    return bot


if __name__ == "__main__":
    mode = os.getenv("MAX_MODE", "long_polling").strip().lower()
    if mode == "cli":
        run_cli_mode()
    else:
        bot = build_bot_from_env()
        assistant = create_library_assistant()
        if assistant:
            print("DeepSeek: подключён")
        else:
            print("DeepSeek: не настроен (создайте deepseek_token.txt для ИИ-ответов)")
        if mode == "webhook":
            host = os.getenv("MAX_WEBHOOK_HOST", "0.0.0.0").strip()
            port = int(os.getenv("MAX_WEBHOOK_PORT", "8080").strip())
            path = os.getenv("MAX_WEBHOOK_PATH", "/webhook").strip()
            public_url = os.getenv("MAX_WEBHOOK_PUBLIC_URL", "").strip()
            if public_url:
                secret = os.getenv("MAX_WEBHOOK_SECRET", "").strip() or None
                try:
                    resp = bot.client.set_webhook_subscription(public_url.rstrip("/") + path, secret=secret)
                    print(f"Webhook subscription set: {resp}")
                except Exception:
                    print("Failed to set webhook subscription automatically:")
                    traceback.print_exc()
            bot.run_webhook_server(host=host, port=port, webhook_path=path)
        else:
            bot.run_forever(idle_seconds=1.0)

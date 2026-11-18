"""Resolve Telegram file-message links through the PremiumFiletoLink bot."""

from __future__ import annotations

from asyncio import Lock, Queue, TimeoutError as AsyncTimeoutError, run, to_thread, wait_for
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import SplitResult, urlsplit

from core.config import BASE_DIR
from core.utils.env import TELEGRAM_API_HASH, TELEGRAM_API_ID

TELEGRAM_PROCESSING_BOT: str = "PremiumFiletoLink_Bot"
TELEGRAM_PROCESSING_TIMEOUT: float = 90.0
TELEGRAM_USER_SESSION: Path = BASE_DIR / "telegram_user"
_TELEGRAM_PROCESSING_LOCK: Lock = Lock()


def _message_entity_text(text: str, offset: int, length: int) -> str:
    """Slice Telegram entity text using its UTF-16 code-unit offsets."""

    encoded: bytes = text.encode("utf-16-le")
    return encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


def _fast_download_target(message: Any) -> str | None:
    """Return the actual URL target attached to a FAST DOWNLOAD label or button."""

    buttons = getattr(message, "buttons", None) or []

    for row in buttons:
        for button in row:
            label: str = str(getattr(button, "text", "") or "").strip().casefold()

            if "fast download" not in label:
                continue

            target = getattr(button, "url", None)

            if not target and getattr(button, "button", None) is not None:
                target = getattr(button.button, "url", None)

            if target:
                return str(target).strip()

    text: str = str(getattr(message, "raw_text", "") or "")

    for entity in getattr(message, "entities", None) or []:
        offset: int = int(getattr(entity, "offset", -1))
        length: int = int(getattr(entity, "length", 0))
        target = getattr(entity, "url", None)

        if offset < 0 or length <= 0 or not target:
            continue

        label: str = _message_entity_text(text, offset, length).strip().casefold()

        if "fast download" in label:
            return str(target).strip()

    return None


def _message_location(link: str) -> tuple[str, int]:
    """Parse public, preview, and private Telegram message links into chat and ID."""

    parsed: SplitResult = urlsplit(link)
    hostname: str = (parsed.hostname or "").lower()

    if hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        raise ValueError("Expected a Telegram message link from t.me or telegram.me")

    parts: list[str] = [part for part in parsed.path.split("/") if part]

    if parts and parts[0] == "s":
        parts = parts[1:]

    if not parts:
        raise ValueError("Telegram link does not identify a message")

    if parts[0] == "c":
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError("Invalid private Telegram message link")

        return f"-100{parts[1]}", int(parts[2])

    if parts[0].isdigit():
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError("Invalid Telegram message link")

        return f"-100{parts[0]}", int(parts[1])

    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError("Telegram link does not identify a supported message")

    return parts[0], int(parts[1])


async def resolve_telegram_message_link(link: str) -> tuple[str, str]:
    """Forward the attached Telegram message and return its verified FAST DOWNLOAD URL and name.

    The Telegram user must have already authenticated with
    `python -m core.utils.telegram_link --login`.
    """

    async with _TELEGRAM_PROCESSING_LOCK:
        return await _resolve_telegram_message_link(link)


async def _resolve_telegram_message_link(link: str) -> tuple[str, str]:
    """Perform message lookup, forwarding, bot-response parsing, and redirect resolution."""

    from telethon import TelegramClient, events
    from telethon.errors import RPCError

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise RuntimeError("Telegram API_ID/API_HASH are missing from core.utils.env")

    try:
        chat, message_id = _message_location(link)
    except ValueError as error:
        raise ValueError(str(error)) from error

    client = TelegramClient(str(TELEGRAM_USER_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    responses: Queue[Any] = Queue()

    async def receive_bot_message(event: Any) -> None:
        """Queue new or edited messages sent by the configured processing bot."""

        try:
            sender = await event.get_sender()
            if str(getattr(sender, "username", "") or "").casefold() == TELEGRAM_PROCESSING_BOT.casefold():
                responses.put_nowait(event.message)
        except Exception:
            # Ignore unrelated incoming messages and entity-resolution failures.
            return

    try:
        try:
            await wait_for(client.connect(), timeout=15)
        except AsyncTimeoutError as error:
            raise TimeoutError("Could not connect to Telegram within 15 seconds") from error

        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram user session is not authenticated. Run "
                "'python -m core.utils.telegram_link --login' once and complete the Telegram login prompts."
            )

        try:
            source_message = await wait_for(client.get_messages(chat, ids=message_id), timeout=20)
        except (RPCError, AsyncTimeoutError) as error:
            raise RuntimeError(f"Could not access Telegram message: {type(error).__name__}") from error

        source_file = getattr(source_message, "file", None) if source_message is not None else None
        if source_message is None or not getattr(source_message, "media", None) or source_file is None:
            raise ValueError("Telegram message was not found or does not contain a downloadable file attachment")

        client.add_event_handler(receive_bot_message, events.NewMessage(incoming=True))

        try:
            try:
                forwarded = await wait_for(
                    client.forward_messages(TELEGRAM_PROCESSING_BOT, source_message),
                    timeout=20,
                )
            except (RPCError, AsyncTimeoutError) as error:
                raise RuntimeError(f"Could not forward the Telegram file to @{TELEGRAM_PROCESSING_BOT}: {type(error).__name__}") from error

            if not forwarded:
                raise RuntimeError(f"Telegram did not confirm forwarding the file to @{TELEGRAM_PROCESSING_BOT}")

            deadline: float = monotonic() + TELEGRAM_PROCESSING_TIMEOUT
            fast_url: str | None = None
            received_bot_response: bool = False

            while fast_url is None:
                remaining: float = deadline - monotonic()

                if remaining <= 0:
                    if received_bot_response:
                        raise ValueError(
                            f"@{TELEGRAM_PROCESSING_BOT} responded, but no FAST DOWNLOAD hyperlink "
                            f"arrived within {int(TELEGRAM_PROCESSING_TIMEOUT)} seconds"
                        )

                    raise TimeoutError(
                        f"@{TELEGRAM_PROCESSING_BOT} did not respond within "
                        f"{int(TELEGRAM_PROCESSING_TIMEOUT)} seconds"
                    )

                try:
                    response = await wait_for(responses.get(), timeout=remaining)
                except AsyncTimeoutError as error:
                    if received_bot_response:
                        raise ValueError(
                            f"@{TELEGRAM_PROCESSING_BOT} responded, but no FAST DOWNLOAD hyperlink "
                            f"arrived within {int(TELEGRAM_PROCESSING_TIMEOUT)} seconds"
                        ) from error

                    raise TimeoutError(
                        f"@{TELEGRAM_PROCESSING_BOT} did not respond within "
                        f"{int(TELEGRAM_PROCESSING_TIMEOUT)} seconds"
                    ) from error

                received_bot_response = True
                fast_url = _fast_download_target(response)

            parsed_url = urlsplit(fast_url)

            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise ValueError("The FAST DOWNLOAD button did not contain a valid HTTP(S) URL")

            # Follow redirects without buffering the file. If the server rejects GET/HEAD,
            # retain the actual verified button target for StoreLimitless's normal downloader.
            from requests import get
            from requests.exceptions import RequestException

            try:
                response = await wait_for(
                    to_thread(
                        get,
                        fast_url,
                        allow_redirects=True,
                        stream=True,
                        timeout=(8, 15),
                        headers={"User-Agent": "Mozilla/5.0 StoreLimitless"},
                    ),
                    timeout=25,
                )

                try:
                    final_url: str = str(response.url or fast_url)
                finally:
                    response.close()

            except (RequestException, AsyncTimeoutError):
                final_url = fast_url

            final_parsed = urlsplit(final_url)

            if final_parsed.scheme not in {"http", "https"} or not final_parsed.hostname:
                raise ValueError("FAST DOWNLOAD redirected to an invalid URL")

            source_name: str = str(getattr(source_file, "name", "") or "Telegram download")
            return final_url, source_name

        finally:
            client.remove_event_handler(receive_bot_message, events.NewMessage(incoming=True))

    finally:
        await client.disconnect()


async def login_telegram_user_session() -> None:
    """Interactively authenticate the separate Telegram user session."""

    from telethon import TelegramClient

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise RuntimeError("Telegram API_ID/API_HASH are missing from core.utils.env")

    client = TelegramClient(str(TELEGRAM_USER_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.start()
        account = await client.get_me()

        if account is None:
            raise RuntimeError("Telegram returned no account after login")

        print(f"Telegram user session authenticated as @{account.username or account.id}.")
        print(f"Session stored at: {TELEGRAM_USER_SESSION}")

    finally:
        await client.disconnect()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["--login"]:
        raise SystemExit("Usage: python -m core.utils.telegram_link --login")

    run(login_telegram_user_session())

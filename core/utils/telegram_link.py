"""Resolve Telegram file-message links through the PremiumFiletoLink bot."""

from __future__ import annotations

from asyncio import Lock, Queue, TimeoutError as AsyncTimeoutError, run, to_thread, wait_for
import json
from pathlib import Path
import re
import unicodedata
from collections.abc import Iterable
from time import monotonic
from typing import Protocol, cast
from urllib.parse import SplitResult, urlsplit

from core.config import BASE_DIR
from core.utils import env as env_config, write_log
from core.utils.env import TELEGRAM_API_HASH, TELEGRAM_API_ID
from core.utils.telegram_ import Telegram


class TelegramEventLike(Protocol):
    """Describe the message-event interface used by the Telethon callback."""

    message: object
    chat_id: int | None

    async def get_sender(self) -> object: ...


TELEGRAM_PROCESSING_BOT: str = "PremiumFiletoLink_Bot"
TELEGRAM_PROCESSING_TIMEOUT: float = 90.0
TELEGRAM_USER_SESSION: Path = BASE_DIR / "telegram_user"
_TELEGRAM_PROCESSING_LOCK: Lock = Lock()


def _message_entity_text(text: str, offset: int, length: int) -> str:
    """Slice Telegram entity text using its UTF-16 code-unit offsets."""

    encoded: bytes = text.encode("utf-16-le")
    return encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


_SMALL_CAPITAL_TRANSLATION: dict[int, str] = str.maketrans({
    "ᴀ": "a",
    "ʙ": "b",
    "ᴄ": "c",
    "ᴅ": "d",
    "ᴇ": "e",
    "ꜰ": "f",
    "ɢ": "g",
    "ʜ": "h",
    "ɪ": "i",
    "ᴊ": "j",
    "ᴋ": "k",
    "ʟ": "l",
    "ᴍ": "m",
    "ɴ": "n",
    "ᴏ": "o",
    "ᴘ": "p",
    "ʀ": "r",
    "ꜱ": "s",
    "ᴛ": "t",
    "ᴜ": "u",
    "ᴠ": "v",
    "ᴡ": "w",
    "ʏ": "y",
    "ᴢ": "z",
})


def _normalized_link_label(value: object) -> str:
    """Normalize stylized Unicode button labels so matching ignores font variants."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).translate(_SMALL_CAPITAL_TRANSLATION).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _message_buttons(message: object) -> list[object]:
    """Return Telegram buttons from either Telethon's wrapper or raw reply markup."""

    buttons_value: object = getattr(message, "buttons", None) or []
    buttons: Iterable[Iterable[object]] = cast("Iterable[Iterable[object]]", buttons_value)
    flattened: list[object] = [button for row in buttons for button in (row or [])]

    if flattened:
        return flattened

    markup = getattr(message, "reply_markup", None)
    rows_value: object = getattr(markup, "rows", None) or []
    rows: Iterable[object] = cast("Iterable[object]", rows_value)
    return [
        button
        for row in rows
        for button in cast("Iterable[object]", getattr(row, "buttons", None) or [])
    ]


def _button_label(button: object) -> str:
    """Read a button label from Telethon's custom wrapper or its raw TL object."""

    raw_button = getattr(button, "button", None)
    return str(
        getattr(button, "text", None)
        or getattr(raw_button, "text", None)
        or ""
    ).strip()


def _button_url(button: object) -> str | None:
    """Read a URL from a custom message button or the underlying Telegram button."""

    raw_button = getattr(button, "button", None)

    for candidate in (
        getattr(button, "url", None),
        getattr(raw_button, "url", None),
    ):
        target = str(candidate or "").strip()
        if target:
            return target

    return None


def _visible_http_urls(text: str) -> list[str]:
    """Extract HTTP(S) URLs written directly in a Telegram message."""

    matches = re.findall(r"""https?://[^\s<>"']+""", text or "", flags=re.IGNORECASE)
    return [match.rstrip(".,;:!?)]}") for match in matches if match.rstrip(".,;:!?)]}")]


def _fast_download_target(message: object) -> str | None:
    """Return the URL behind a FAST DOWNLOAD button, entity, or visible link."""

    text: str = str(getattr(message, "raw_text", "") or getattr(message, "message", "") or "")
    normalized_text = _normalized_link_label(text)
    buttons = _message_buttons(message)
    matching_button_found = False

    # Prefer the exact DOWNLOAD button over STREAM or any other download-labelled
    # button. This is the file endpoint the import pipeline needs, not the web player.
    # Normalize Unicode small caps because the bot renders labels in stylized text.
    for button in buttons:
        label = _normalized_link_label(_button_label(button))
        target = _button_url(button)

        if label == "download" and target:
            return target

    # Keep compatibility with bot variants that add emoji or extra words to the
    # download button label, but only after checking for the exact DOWNLOAD label.
    for button in buttons:
        label = _normalized_link_label(_button_label(button))
        target = _button_url(button)

        if "download" in label and target:
            return target

    # Some bot variants only expose a STREAM button. Keep it as a fallback,
    # although it may resolve to a web page rather than a direct file response.
    for button in buttons:
        label = _normalized_link_label(_button_label(button))
        target = _button_url(button)

        if "stream" in label and target:
            return target

    for button in buttons:
        label = _normalized_link_label(_button_label(button))

        if "fastdownload" not in label:
            continue

        matching_button_found = True
        target = _button_url(button)
        if target:
            return target

    # A text URL can be represented as a Telegram MessageEntityTextUrl, where
    # the actual destination is stored on the entity rather than in message text.
    for entity in getattr(message, "entities", None) or []:
        offset: int = int(getattr(entity, "offset", -1))
        length: int = int(getattr(entity, "length", 0))
        target = str(getattr(entity, "url", "") or "").strip()

        if offset < 0 or length <= 0 or not target:
            continue

        label = _normalized_link_label(_message_entity_text(text, offset, length))
        if "fastdownload" in label:
            return target

    # Some bot versions send the destination as visible text instead of attaching
    # it to the labeled button/entity. Use it only when the message identifies the
    # FAST DOWNLOAD action and there is a single unambiguous HTTP(S) URL.
    if "fastdownload" in normalized_text or matching_button_found:
        visible_urls = _visible_http_urls(text)
        if len(visible_urls) == 1:
            return visible_urls[0]

    return None


async def _fast_download_callback_target(message: object) -> str | None:
    """Click a FAST DOWNLOAD callback button when the bot does not attach a URL."""

    for button in _message_buttons(message):
        if "fastdownload" not in _normalized_link_label(_button_label(button)):
            continue

        if _button_url(button):
            continue

        raw_button = getattr(button, "button", None)
        button_type = type(raw_button).__name__.casefold()

        # A callback button has no URL until its bot-side action is invoked. Do
        # not click ordinary text/reply buttons, which would send a chat message.
        if "callback" not in button_type:
            continue

        click = getattr(button, "click", None)
        if not callable(click):
            continue

        try:
            answer = await wait_for(click(), timeout=12)
        except Exception:
            continue

        target = str(getattr(answer, "url", "") or "").strip()
        if target:
            return target

        answer_text = str(
            getattr(answer, "message", None)
            or getattr(answer, "text", None)
            or ""
        )
        # The callback was reached through a FAST DOWNLOAD button, so an
        # unambiguous URL in its answer is a valid result even if the bot's
        # popup omits the button label.
        answer_urls = _visible_http_urls(answer_text)
        if len(answer_urls) == 1:
            return answer_urls[0]

    return None

def _message_location(link: str) -> tuple[str, int]:
    """Parse a publicly accessible Telegram message link into its chat and message ID."""

    parsed: SplitResult = urlsplit(link)
    hostname: str = (parsed.hostname or "").lower()

    if parsed.scheme.lower() not in {"http", "https"} or hostname not in {
        "t.me", "www.t.me", "telegram.me", "www.telegram.me"
    }:
        raise ValueError("Paste a public Telegram message URL beginning with https://t.me/ or https://telegram.me/")

    parts: list[str] = [part for part in parsed.path.split("/") if part]

    if parts and parts[0].casefold() == "s":
        parts = parts[1:]

    if not parts:
        raise ValueError("Telegram link does not identify a message. Use a public link to the actual file message.")

    if parts[0].casefold() == "c":
        raise ValueError(
            "This is a private Telegram /c/ message link. StoreLimitless can only process publicly accessible "
            "Telegram messages. Open the post through its public channel username and use that t.me/<username>/<message-id> link."
        )

    if parts[0].isdigit():
        raise ValueError(
            "This Telegram link uses an internal chat ID and is not publicly accessible. "
            "Use the public t.me/<channel-username>/<message-id> link instead."
        )

    username: str = parts[0]
    if (
        not 5 <= len(username) <= 32
        or not all(character.isascii() and (character.isalnum() or character == "_") for character in username)
        or not username[0].isalpha()
    ):
        raise ValueError(
            "This Telegram link does not contain a public channel or group username. "
            "Use a publicly accessible t.me/<username>/<message-id> link."
        )

    message_segments: list[str] = parts[1:]
    if not message_segments or not all(segment.isdigit() for segment in message_segments):
        raise ValueError(
            "Telegram link does not identify a public message. Use a t.me/<username>/<message-id> link to a post with a file attached."
        )

    # Topic links can include a thread ID before the actual message ID.
    message_id: int = int(message_segments[-1])
    if message_id <= 0:
        raise ValueError("Telegram message ID must be a positive number")

    return username, message_id

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
    from telethon.sessions import StringSession

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise RuntimeError("Telegram API_ID/API_HASH are missing from core.utils.env")

    try:
        chat, message_id = _message_location(link)
    except ValueError as error:
        raise ValueError(str(error)) from error

    session_string: str = str(getattr(env_config, "TELEGRAM_USER_SESSION_STRING", "") or "").strip()
    session = StringSession(session_string) if session_string else str(TELEGRAM_USER_SESSION)
    client = TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    responses: Queue[object] = Queue()

    async def receive_bot_message(event: TelegramEventLike) -> None:
        """Log and queue the full new or edited message from the processing bot."""

        try:
            sender = await event.get_sender()
            if str(getattr(sender, "username", "") or "").casefold() != TELEGRAM_PROCESSING_BOT.casefold():
                return

            message = event.message
            message_buttons = _message_buttons(message)
            response_details = {
                "message_id": getattr(message, "id", None),
                "sender_username": getattr(sender, "username", None),
                "sender_id": getattr(sender, "id", None),
                "chat_id": getattr(event, "chat_id", None),
                "date": str(getattr(message, "date", None)),
                "raw_text": getattr(message, "raw_text", None),
                "message_text": getattr(message, "message", None),
                "entities": [repr(entity) for entity in (getattr(message, "entities", None) or [])],
                "buttons": [
                    {
                        "text": _button_label(button),
                        "url": _button_url(button),
                        "raw_button": repr(getattr(button, "button", button)),
                    }
                    for button in message_buttons
                ],
                "reply_markup": repr(getattr(message, "reply_markup", None)),
                "full_message_repr": repr(message),
            }

            try:
                write_log(
                    "INFO",
                    Telegram,
                    "FILETOLINK RESPONSE",
                    str(getattr(sender, "username", None) or getattr(sender, "id", "unknown")),
                    json.dumps(response_details, ensure_ascii=False, indent=2, default=repr),
                )
            except Exception:
                # Diagnostics must never prevent a valid bot response from being processed.
                pass

            responses.put_nowait(message)
        except Exception:
            # Ignore unrelated incoming messages and entity-resolution failures.
            return

    try:
        try:
            await wait_for(client.connect(), timeout=15)
        except TimeoutError as error:
            raise TimeoutError("Could not connect to Telegram within 15 seconds") from error

        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram link processing is not configured yet: the Telegram user session is not authenticated. "
                "Run 'telegram-login --new' at the StoreLimitless terminal prompt and sign in with your personal "
                "Telegram account."
            )

        account = await client.get_me()

        if account is None:
            raise RuntimeError("Telegram did not return the account for the configured user session.")

        if getattr(account, "bot", False):
            raise RuntimeError(
                "TELEGRAM_USER_SESSION_STRING is authenticated as a Telegram BOT account, not a personal user "
                "account. Message-history lookup requires a user session. Run 'telegram-login --new', sign in "
                "with your personal Telegram phone number, then replace TELEGRAM_USER_SESSION_STRING in your "
                "private core/utils/env.py with the newly printed value."
            )

        try:
            chat_entity = await wait_for(client.get_entity(chat), timeout=20)
            source_message = await wait_for(client.get_messages(chat_entity, ids=message_id), timeout=20)

            if source_message is None:
                async def find_message_in_history() -> object | None:
                    """Retry a direct message lookup through the group's history."""

                    async for candidate in client.iter_messages(
                        chat_entity,
                        min_id=message_id - 1,
                        max_id=message_id + 1,
                        limit=3,
                    ):
                        if int(getattr(candidate, "id", 0)) == message_id:
                            return candidate

                    return None

                source_message = await wait_for(find_message_in_history(), timeout=20)
        except (TimeoutError, RPCError) as error:
            error_name: str = type(error).__name__
            if error_name in {"ChannelPrivateError", "ChannelInvalidError", "UsernameInvalidError", "UsernameNotOccupiedError"}:
                raise ValueError(
                    "Telegram could not access this message publicly. The channel may be private, deleted, or unavailable. "
                    "Use a public t.me/<channel-username>/<message-id> link."
                ) from error
            if error_name == "MessageIdInvalidError":
                raise ValueError(
                    "Telegram could not find that public message. Check the message ID and make sure the post is still available."
                ) from error
            if isinstance(error, AsyncTimeoutError):
                raise TimeoutError(
                    "Timed out while checking the public Telegram message. Check the link and try again."
                ) from error
            if error_name == "BotMethodInvalidError":
                raise RuntimeError(
                    "Telegram rejected a user-only message-history request. The configured Telegram session "
                    "is likely a bot session. Run 'telegram-login --new' and save the generated personal-user "
                    "session string in core/utils/env.py."
                ) from error
            raise RuntimeError(
                f"Could not access the public Telegram message ({error_name}). "
                "Check that the channel and attached post are still publicly accessible."
            ) from error

        if source_message is None:
            raise ValueError(
                f"Telegram resolved @{chat} (ID {getattr(chat_entity, 'id', 'unknown')}), "
                f"but neither direct lookup nor history lookup returned message {message_id}. "
                "This does not by itself mean the group is private or that you are not a member. "
                "Confirm that the exact message still opens in Telegram and that the deployed "
                "TELEGRAM_USER_SESSION_STRING belongs to the account you expect. The lookup may also "
                "fail if Telegram no longer exposes that message through the API."
            )

        source_file = getattr(source_message, "file", None)
        if not getattr(source_message, "media", None) or source_file is None:
            raise ValueError(
                "The public Telegram message was found, but it does not contain a downloadable file attachment. "
                "Paste a link to the message that contains the actual file."
            )

        client.add_event_handler(receive_bot_message, events.NewMessage(incoming=True))
        client.add_event_handler(receive_bot_message, events.MessageEdited())

        try:
            try:
                forwarded = await wait_for(
                    client.forward_messages(TELEGRAM_PROCESSING_BOT, source_message),
                    timeout=20,
                )
            except (TimeoutError, RPCError) as error:
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
                except TimeoutError as error:
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

                if fast_url is None:
                    # Some FiletoLink responses expose FAST DOWNLOAD as a
                    # Telegram callback button; its URL only arrives after it
                    # is clicked through the authenticated user session.
                    fast_url = await _fast_download_callback_target(response)

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

            except (TimeoutError, RequestException):
                final_url = fast_url

            final_parsed = urlsplit(final_url)

            if final_parsed.scheme not in {"http", "https"} or not final_parsed.hostname:
                raise ValueError("FAST DOWNLOAD redirected to an invalid URL")

            source_name: str = str(getattr(source_file, "name", "") or "Telegram download")
            return final_url, source_name

        finally:
            client.remove_event_handler(receive_bot_message, events.NewMessage(incoming=True))
            client.remove_event_handler(receive_bot_message, events.MessageEdited())

    finally:
        await client.disconnect()


async def login_telegram_user_session(force_new: bool = False) -> None:
    """Authenticate a personal Telegram account and print a portable session string."""

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise RuntimeError("Telegram API_ID/API_HASH are missing from core.utils.env")

    session = StringSession() if force_new else str(TELEGRAM_USER_SESSION)
    client = TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.start()
        account = await client.get_me()

        if account is None:
            raise RuntimeError("Telegram returned no account after login")

        if getattr(account, "bot", False):
            raise RuntimeError(
                "The session file is authenticated as a Telegram BOT account, not a personal user account. "
                "Run 'telegram-login --new' to create a fresh session, then sign in with your personal phone number."
            )

        print(f"Telegram user session authenticated as @{account.username or account.id}.")

        if not force_new:
            print(f"Session stored at: {TELEGRAM_USER_SESSION}")

        exported_session: str = StringSession.save(client.session)
        print("Copy this line into your private core/utils/env.py file to persist the login across redeployments:")
        print(f'TELEGRAM_USER_SESSION_STRING: str = "{exported_session}"')

    finally:
        await client.disconnect()


if __name__ == "__main__":
    import sys

    arguments: list[str] = sys.argv[1:]

    if arguments not in (["--login"], ["--login", "--new"]):
        raise SystemExit("Usage: python -m core.utils.telegram_link --login [--new]")

    run(login_telegram_user_session(force_new=arguments == ["--login", "--new"]))

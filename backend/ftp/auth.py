from asyncio import run_coroutine_threadsafe
from typing import Any

from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer

from backend.database.models import User
from backend.server.security import verify_password


class StoreLimitlessAuthorizer(DummyAuthorizer):

    def validate_authentication(
            self,
            username: str,
            password: str,
            handler: Any,
    ) -> None:
        user = run_coroutine_threadsafe(
            User.get(username),
            handler.event_loop,
        ).result()

        if user is None or not verify_password(password, user.password):
            raise AuthenticationFailed("Authentication failed.")

        handler.store_limitless_user = user

    def get_home_dir(self, username: str) -> str:
        return "/"

    def has_perm(
            self,
            username: str,
            perm: str,
            path: str | None = None,
    ) -> bool:
        return perm in "elradfmwMT"

    def get_msg_login(self, username: str) -> str:
        return "Login successful."

    def get_msg_quit(self, username: str) -> str:
        return "Goodbye."

    def get_perms(self, username: str) -> str:
        return "elradfmwMT"

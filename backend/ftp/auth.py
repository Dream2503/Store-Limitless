"""FTP authentication and permission handling for StoreLimitless users."""

from asyncio import run_coroutine_threadsafe
from typing import Any

from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer

from backend.database.models import User
from backend.server.security import verify_password
from core.data_center import Database
from core.utils import write_log


class StoreLimitlessAuthorizer(DummyAuthorizer):
    """Authenticate FTP users against StoreLimitless and define their FTP permissions."""

    def validate_authentication(self, username: str, password: str, handler: Any) -> None:
        """Load the user on the application's event loop and verify the supplied password."""

        user: User | None = run_coroutine_threadsafe(User.get(username), handler.event_loop).result()

        if user is None or not verify_password(password, user.password):
            write_log("WARNING", Database, "FTP AUTH", username, "Authentication rejected")
            raise AuthenticationFailed("Authentication failed.")

        handler.store_limitless_user = user
        write_log("INFO", Database, "FTP AUTH", username, "Authentication successful")

    def get_home_dir(self, username: str) -> str:
        """Return the virtual root exposed to an authenticated FTP user."""

        return "/"

    def has_perm(self, username: str, perm: str, path: str | None = None) -> bool:
        """Check whether the requested FTP permission is supported."""

        return perm in {"e", "l", "r", "a", "d", "f", "m", "w", "M", "T"}

    def get_msg_login(self, username: str) -> str:
        """Return the successful-login message sent to the FTP client."""

        return "Login successful."

    def get_msg_quit(self, username: str) -> str:
        """Return the logout message sent to the FTP client."""

        return "Goodbye."

    def get_perms(self, username: str) -> str:
        """Return the supported FTP permission letters."""

        return "elradfmwMT"

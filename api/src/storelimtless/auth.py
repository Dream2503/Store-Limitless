from __future__ import annotations

from typing import TYPE_CHECKING

import requests
from requests import Response

from .exception import StoreLimitlessHTTPError, StoreLimitlessConnectionError, StoreLimitlessError, StoreLimitlessResponseError

if TYPE_CHECKING:
    from .user import User


class StoreLimitless:
    API_URL: str = "http://127.0.0.1:8000"

    def __new__(cls, *args, **kwargs):
        raise TypeError("MyClass cannot be instantiated")

    @staticmethod
    def request(token: str | None, method: str, path: str, **kwargs) -> Response:
        try:
            response: Response = requests.request(
                method,
                f"{StoreLimitless.API_URL}{path}",
                headers={
                    **({"Authorization": f"Bearer {token}"} if token else {}),
                    **kwargs.pop("headers", {}),
                },
                **kwargs,
            )
        except requests.ConnectionError as e:
            raise StoreLimitlessConnectionError(f"Could not connect to StoreLimitless server at {StoreLimitless.API_URL}") from e

        except requests.Timeout as e:
            raise StoreLimitlessConnectionError("Request to StoreLimitless server timed out") from e

        except requests.RequestException as e:
            raise StoreLimitlessError(f"Request to StoreLimitless server failed: {e}") from e

        if not response.ok:
            try:
                message: str = response.json().get("detail", response.text)

            except (ValueError, AttributeError):
                message: str = response.text

            raise StoreLimitlessHTTPError(response.status_code, message or f"HTTP {response.status_code}")

        return response

    @classmethod
    def login(cls, username: str, password: str) -> User:
        from .user import User

        response: Response = cls.request(None, "POST", "/auth/login", params={"username": username, "password": password})

        try:
            reply, user, home = response.json()
            return User(reply["access_token"], **user, home=home)

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid login response") from e

    @classmethod
    def register(cls, first_name: str, last_name: str, username: str, password: str) -> None:
        cls.request(
            None,
            "POST",
            "/auth/register",
            json={
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "password": password,
            },
        )

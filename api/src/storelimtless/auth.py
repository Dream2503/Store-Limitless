import requests

from .client import StoreLimitlessClient
from .exception import StoreLimitlessHTTPError, StoreLimitlessConnectionError, StoreLimitlessError, StoreLimitlessResponseError


class StoreLimitless:
    BASE_URL: str = "http://localhost:8000"

    @staticmethod
    def http_error(response: requests.Response) -> StoreLimitlessHTTPError:
        try:
            message = response.json().get("detail", response.text)
        except (ValueError, AttributeError):
            message = response.text

        return StoreLimitlessHTTPError(
            response.status_code,
            message or f"HTTP {response.status_code}",
        )

    @classmethod
    def login(cls, username: str, password: str) -> StoreLimitlessClient:
        try:
            response = requests.post(
                f"{cls.BASE_URL}/auth/login",
                json={
                    "username": username,
                    "password": password,
                },
                timeout=10,
            )
        except requests.ConnectionError as e:
            raise StoreLimitlessConnectionError(f"Could not connect to StoreLimitless server at {cls.BASE_URL}") from e

        except requests.Timeout as e:
            raise StoreLimitlessConnectionError("Request to StoreLimitless server timed out") from e

        except requests.RequestException as e:
            raise StoreLimitlessError(f"Request to StoreLimitless server failed: {e}") from e

        if not response.ok:
            raise cls.http_error(response)

        try:
            token = response.json()["access_token"]

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid login response") from e

        return StoreLimitlessClient(token)

    @classmethod
    def register(cls, first_name: str, last_name: str, username: str, password: str) -> None:
        try:
            response = requests.post(
                f"{cls.BASE_URL}/auth/register",
                json={
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": username,
                    "password": password,
                },
                timeout=10,
            )
        except requests.ConnectionError as e:
            raise StoreLimitlessConnectionError(f"Could not connect to StoreLimitless server at {cls.BASE_URL}") from e

        except requests.Timeout as e:
            raise StoreLimitlessConnectionError("Request to StoreLimitless server timed out") from e

        except requests.RequestException as e:
            raise StoreLimitlessError(f"Request to StoreLimitless server failed: {e}") from e

        if not response.ok:
            raise cls.http_error(response)

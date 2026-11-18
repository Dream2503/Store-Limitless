import requests

from .directory import Directory
from .exception import StoreLimitlessConnectionError, StoreLimitlessError


class StoreLimitlessClient:
    def __init__(self, token: str):
        self.token = token
        self._directory = Directory(self, token)

    @property
    def directory(self) -> Directory:
        return self._directory

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        from api.src.storelimtless.auth import StoreLimitless

        try:
            response = requests.request(
                method,
                f"{StoreLimitless.BASE_URL}{path}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    **kwargs.pop("headers", {}),
                },
                timeout=10,
                **kwargs,
            )
        except requests.ConnectionError as e:
            raise StoreLimitlessConnectionError(f"Could not connect to StoreLimitless server at {StoreLimitless.BASE_URL}") from e

        except requests.Timeout as e:
            raise StoreLimitlessConnectionError("Request to StoreLimitless server timed out") from e

        except requests.RequestException as e:
            raise StoreLimitlessError(f"Request to StoreLimitless server failed: {e}") from e

        if not response.ok:
            raise StoreLimitless.http_error(response)

        return response

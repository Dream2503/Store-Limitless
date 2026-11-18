class StoreLimitlessError(Exception): ...


class StoreLimitlessConnectionError(StoreLimitlessError): ...


class StoreLimitlessResponseError(StoreLimitlessError): ...


class StoreLimitlessHTTPError(StoreLimitlessError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)

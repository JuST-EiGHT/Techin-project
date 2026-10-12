"""Исключения клиента Ozon. Тексты — для пользователя, на русском, без Api-Key."""


class OzonAPIError(Exception):
    """Базовая ошибка Ozon Seller API.

    status_code   — HTTP-статус ответа Ozon (None, если ответа не было)
    ozon_message  — текст ошибки от Ozon как есть (для логов и отладки, Api-Key вырезан)
    """

    default_message = "Ozon отклонил запрос"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        ozon_message: str = "",
        path: str = "",
    ) -> None:
        self.message = message or self.default_message
        self.status_code = status_code
        self.ozon_message = ozon_message
        self.path = path
        super().__init__(self.message)


class OzonAuthError(OzonAPIError):
    """Ключ не принят: 401/403 или известный текст ошибки ключа от Ozon.

    kind — причина (по текстам «Частых ошибок» документации Ozon):
      invalid      — неверный Client-Id / Api-Key
      deactivated  — ключ деактивирован
      no_role      — у ключа нет роли для метода
      ip_restricted — ключ разрешён только с определённых IP-адресов
      forbidden    — прочий отказ в доступе
    """

    default_message = "Ozon не принял API-ключ"

    def __init__(self, message: str | None = None, *, kind: str = "invalid", **kw) -> None:
        self.kind = kind
        super().__init__(message, **kw)


class OzonRateLimitError(OzonAPIError):
    """429: превышен лимит запросов, повторы не помогли."""

    default_message = "Ozon ограничил частоту запросов, попробуйте через минуту"


class OzonServerError(OzonAPIError):
    """5xx: ошибка на стороне Ozon, повторы не помогли."""

    default_message = "Ozon временно не отвечает (ошибка на стороне Ozon), попробуйте позже"


class OzonTimeoutError(OzonAPIError):
    """Ozon не ответил за отведённое время."""

    default_message = "Ozon не ответил вовремя, попробуйте позже"


class OzonNetworkError(OzonAPIError):
    """Нет соединения с Ozon (DNS, обрыв, отказ в соединении)."""

    default_message = "Нет связи с Ozon, попробуйте позже"


class OzonNotConfiguredError(OzonAPIError):
    """У пользователя нет рабочего API-ключа Ozon."""

    default_message = "Не подключён действующий API-ключ Ozon — добавьте его в разделе «API-ключи»"

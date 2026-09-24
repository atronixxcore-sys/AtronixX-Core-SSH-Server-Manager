class TelegramError(Exception): pass
class BadRequest(TelegramError): pass
class Forbidden(TelegramError): pass
class NetworkError(TelegramError): pass
class TimedOut(NetworkError): pass
class RetryAfter(TelegramError):
    def __init__(self, retry_after): super().__init__("retry"); self.retry_after = retry_after

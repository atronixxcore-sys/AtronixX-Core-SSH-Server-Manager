from types import SimpleNamespace
class ApplicationHandlerStop(Exception): pass
class ContextTypes: DEFAULT_TYPE = object
class BaseUpdateProcessor:
    def __init__(self, max_concurrent_updates=1): pass
class Application: pass
class TypeHandler:
    def __init__(self, typ, cb): self.cb = cb; self.kind = "type"
class CommandHandler:
    def __init__(self, name, cb, filters=None): self.name, self.cb, self.kind = name, cb, "command"
class CallbackQueryHandler:
    def __init__(self, cb, pattern=None): self.cb, self.pattern, self.kind = cb, pattern, "callback"
class MessageHandler:
    def __init__(self, flt, cb): self.flt, self.cb, self.kind = flt, cb, "message"
class _F:
    def __init__(self, n): self.name = n
    def __and__(self, o): return _F(self.name + "&" + o.name)
    def __invert__(self): return _F("~" + self.name)
class filters:
    ChatType = SimpleNamespace(PRIVATE=_F("private"))
    TEXT = _F("text"); COMMAND = _F("command"); PHOTO = _F("photo")
    Document = SimpleNamespace(ALL=_F("document"))

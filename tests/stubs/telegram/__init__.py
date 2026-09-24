class InlineKeyboardButton:
    def __init__(self, text, callback_data=None, url=None, api_kwargs=None, **kw):
        self.text, self.callback_data, self.url, self.kw = text, callback_data, url, kw
class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard): self.inline_keyboard = inline_keyboard
class BotCommand:
    def __init__(self, c, d): self.command, self.description = c, d
class Update:
    def __init__(self, **kw):
        self.effective_user = kw.get("effective_user"); self.effective_chat = kw.get("effective_chat")
        self.message = kw.get("message"); self.callback_query = kw.get("callback_query")
class Message: pass

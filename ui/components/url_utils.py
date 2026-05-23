import re

_CLIPBOARD_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_URL_TRAILING_JUNK = frozenset(".,;)\"'>]")

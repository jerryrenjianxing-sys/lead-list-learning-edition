"""Drop credential values and free-form response bodies before product logging."""

import logging
import re
from urllib.parse import urlsplit, urlunsplit


def sanitize(message):
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(message)).strip()
    message = re.sub(
        r"(?i)(cookie|authorization|set-cookie|xsec_token|msToken|access_token|refresh_token|sessionid|csrf)(\s*[:=]\s*)[^\r\n]+",
        r"\1\2[redacted]",
        message,
    )

    def local_url(match):
        parts = urlsplit(match[0])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    message = re.sub(r"https?://[^\s\"\'<>]+", local_url, message)
    message = re.sub(
        r"(?i)((?:response(?:\.text|_text| data)?|(?:notes_)?res|search result|search contents|request error|answers)\s*[:=]).*",
        r"\1 [body omitted]",
        message,
    )
    message = re.sub(r"(?:\{|\[\s*[\"\']).*", "[structured content omitted]", message)
    message = re.sub(
        r"<(?:!doctype|html|body)[\s\S]*", "[body omitted]", message, flags=re.I
    )
    return message[:4000]


class ProductLogFilter(logging.Filter):
    def filter(self, record):
        record.msg = sanitize(record.getMessage())
        record.args = ()
        return True


def install():
    logging.getLogger("MediaCrawler").addFilter(ProductLogFilter())

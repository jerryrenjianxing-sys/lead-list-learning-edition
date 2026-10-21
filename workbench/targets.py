"""Canonical public target IDs for planned scopes; unresolved short URLs stay explicit."""

import re
from urllib.parse import urlsplit


def canonical(platform, capability, value):
    if capability == "search" or not value.startswith(("http://", "https://")):
        return value
    path = urlsplit(value).path
    patterns = {
        ("dy", "detail"): r"/video/(\d+)",
        ("dy", "creator"): r"/user/([^/]+)",
        ("xhs", "detail"): r"/(?:explore|discovery/item)/([^/]+)",
        ("xhs", "creator"): r"/user/profile/([^/]+)",
        ("ks", "detail"): r"/(?:short-video|fw/photo)/([^/]+)",
        ("ks", "creator"): r"/profile/([^/]+)",
        ("bili", "detail"): r"/video/([^/]+)",
        ("bili", "creator"): r"/([0-9]+)",
        ("wb", "detail"): r"/(?:detail|status)/(\w+)",
        ("wb", "creator"): r"/(?:u/)?(\d+)",
        ("tieba", "detail"): r"/p/(\d+)",
        ("zhihu", "detail"): r"/(?:p|zvideo)/(\d+)",
        ("zhihu", "creator"): r"/(?:people|org)/([^/]+)",
    }
    if platform == "zhihu" and capability == "detail":
        answer = re.search(r"/question/(\d+)/answer/(\d+)", path)
        if answer:
            return ":".join(answer.groups())
    match = re.search(patterns.get((platform, capability), r"(?!)"), path)
    return match.group(1) if match else value

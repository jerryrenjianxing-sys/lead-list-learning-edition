"""One upstream revision source in development and packaged builds."""
import json

from .paths import ROOT


def revision():
    return json.loads((ROOT / 'upstream.lock.json').read_text(encoding='utf-8'))['revision']

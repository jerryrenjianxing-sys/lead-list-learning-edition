import base64
from io import BytesIO

from PIL import Image

from tools import crawler_util
from tools.crawler_util import show_qrcode


def test_show_qrcode_only_writes_webui_image(monkeypatch, tmp_path):
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_bytes, format="PNG")
    monkeypatch.setattr(
        Image.Image,
        "show",
        lambda self: (_ for _ in ()).throw(AssertionError("must not open an image viewer")),
    )
    qr_path = tmp_path / ".login_qrcode.png"
    monkeypatch.setattr(crawler_util, "LOGIN_QRCODE_PATH", qr_path, raising=False)

    show_qrcode(base64.b64encode(image_bytes.getvalue()).decode())

    assert qr_path.is_file()


def test_crawl_limit_zero_never_reaches_local_limit():
    assert not crawler_util.crawl_limit_reached(1, 0)
    assert not crawler_util.crawl_limit_reached(2000, 0)
    assert crawler_util.crawl_limit_reached(15, 15)


def test_page_progress_rejects_empty_or_repeated_pages():
    seen = set()
    assert crawler_util.register_page_ids(seen, ["a", "b"])
    assert not crawler_util.register_page_ids(seen, ["a", "b"])
    assert not crawler_util.register_page_ids(seen, [])

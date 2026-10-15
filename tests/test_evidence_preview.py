"""Verify the actual admin callback preserves originals and makes their limits visible."""

import base64
import io
from html import unescape
from html.parser import HTMLParser

import admin_app
import gradio as gr
import pytest
from PIL import Image
from pydantic import SecretStr

from acs.returns import ReturnService

from .test_return_submission import flow as flow
from .test_return_submission import write


class MediaMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.images, self.videos = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"a", "img", "video"}:
            {"a": self.links, "img": self.images, "video": self.videos}[tag].append(dict(attrs))


def preview(flow, data=None):
    settings, store, _, sid = flow
    settings = settings.model_copy(update={"admin_username": "reviewer",
                                           "admin_password": SecretStr("synthetic-review-pass")})
    service = ReturnService(settings, store)
    if data is not None:
        write(store, sid, {"reason": "quality", "problem_description": "单耳无声",
                           "quality_non_user_damage": True})
        tid = store.begin_turn(sid, "USER001", "测试上传")
        service.attach_evidence(sid, "USER001", tid, data)
        store.interrupt_turn(sid, "USER001", tid)
    request = service.confirm_and_submit(
        sid, "USER001", service.preview(sid, "USER001")["preview_id"],
    )
    demo = admin_app.build_app(settings)
    handler = next(f.fn for f in demo.fns.values() if f.fn.__name__ == "evidence")
    return handler(request["request_id"], gr.Request(username="reviewer"))


@pytest.mark.parametrize("format,size", [("PNG", (16, 16)), ("JPEG", (640, 480)),
                                       ("WEBP", (300, 200))])
def test_image_preview_explains_dimensions_and_downloads_exact_original(flow, format, size):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format=format)
    original = output.getvalue()
    text = preview(flow, original)
    markup = MediaMarkup(text)
    assert len(markup.images) == 1
    assert f"{size[0]} × {size[1]}" in unescape(text)
    assert f"{len(original)} 字节" in unescape(text)
    assert "空白" in unescape(text)  # The existing white fixture must not imply readable evidence.
    assert len(markup.links) == 1 and markup.links[0].get("download")
    assert base64.b64decode(markup.links[0]["href"].split(",", 1)[1]) == original
    assert base64.b64decode(markup.images[0]["src"].split(",", 1)[1]) == original


def test_no_attachment_is_explicit_instead_of_an_empty_preview(flow):
    text = preview(flow)
    assert "本申请未附照片或视频" in text
    markup = MediaMarkup(text)
    assert not markup.images and not markup.links


def test_video_keeps_controls_and_offers_original_download(flow):
    # Matches the existing upload header check, not a playable-video assertion.
    original = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
    markup = MediaMarkup(preview(flow, original))
    assert len(markup.videos) == 1 and "controls" in markup.videos[0]
    assert len(markup.links) == 1 and markup.links[0]["download"].endswith(".mp4")
    assert base64.b64decode(markup.links[0]["href"].split(",", 1)[1]) == original

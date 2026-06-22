"""interaction/multimodal.py — parser tests (spec §骨架 line 883, §2.1).

Phase 1 only ships text + image-URI references; Phase 2h fills in
image bytes / MLLM hooks. These tests pin the Phase 1 contract:
malformed input must degrade gracefully (drop the bad part, keep the
good ones) — never raise — so the FastAPI chat endpoint stays robust.
"""

from __future__ import annotations

from cognitiveplane.interaction.multimodal import (
    ModalityPart,
    ModalityType,
    MultiModalMessage,
    MultiModalParser,
)


def test_string_payload_is_single_text_part():
    msg = MultiModalParser().parse("hello world")

    assert isinstance(msg, MultiModalMessage)
    assert len(msg.parts) == 1
    assert msg.parts[0].type is ModalityType.TEXT
    assert msg.parts[0].content == "hello world"
    assert msg.text == "hello world"
    assert msg.has_image is False
    assert msg.has_annotation is False


def test_list_payload_with_text_image_annotation():
    payload = [
        {"type": "text", "content": "describe this"},
        {"type": "image", "content": "s3://bucket/x.png"},
        {"type": "annotation", "content": '{"box":[0,0,10,10]}'},
    ]
    msg = MultiModalParser().parse(payload)

    assert len(msg.parts) == 3
    assert msg.text == "describe this"
    assert msg.has_image is True
    assert msg.has_annotation is True


def test_unknown_modality_dropped_silently():
    payload = [
        {"type": "text", "content": "ok"},
        {"type": "video", "content": "s3://x.mp4"},  # unknown
        {"type": "audio", "content": "s3://x.wav"},  # unknown
    ]
    msg = MultiModalParser().parse(payload)

    assert len(msg.parts) == 1
    assert msg.parts[0].content == "ok"


def test_non_string_content_dropped():
    payload = [
        {"type": "text", "content": "ok"},
        {"type": "text", "content": 12345},  # int — drop
        {"type": "image", "content": None},  # None — drop
    ]
    msg = MultiModalParser().parse(payload)

    assert len(msg.parts) == 1
    assert msg.parts[0].content == "ok"


def test_non_dict_items_dropped():
    payload = [
        {"type": "text", "content": "kept"},
        "raw string in list",  # not a dict
        42,
        None,
    ]
    msg = MultiModalParser().parse(payload)

    assert len(msg.parts) == 1
    assert msg.parts[0].content == "kept"


def test_metadata_preserved_when_dict():
    payload = [
        {
            "type": "image",
            "content": "s3://x.png",
            "metadata": {"width": 1024, "height": 768},
        },
    ]
    msg = MultiModalParser().parse(payload)

    assert msg.parts[0].metadata == {"width": 1024, "height": 768}


def test_metadata_replaced_when_not_dict():
    payload = [{"type": "text", "content": "ok", "metadata": "bad"}]

    msg = MultiModalParser().parse(payload)

    assert msg.parts[0].metadata == {}


def test_empty_payloads_yield_no_parts():
    parser = MultiModalParser()
    assert parser.parse([]).parts == ()
    assert parser.parse({}).parts == ()  # dict is not list/str → no parts
    assert parser.parse(None).parts == ()


def test_text_property_concatenates_multiple_text_parts():
    payload = [
        {"type": "text", "content": "line1"},
        {"type": "image", "content": "s3://x"},
        {"type": "text", "content": "line2"},
    ]
    msg = MultiModalParser().parse(payload)

    assert msg.text == "line1\nline2"


def test_modality_part_is_frozen():
    part = ModalityPart(type=ModalityType.TEXT, content="x")
    try:
        part.content = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ModalityPart should be frozen / immutable")

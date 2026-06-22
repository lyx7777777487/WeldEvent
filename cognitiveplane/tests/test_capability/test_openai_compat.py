"""Tests for capability/openai_compat.py — alias verification."""

from cognitiveplane.capability.openai_compat import (
    OpenAICompatProvider,
    OpenAIProvider,
)


def test_compat_provider_is_openai_provider():
    """OpenAICompatProvider is an alias for OpenAIProvider."""
    assert OpenAICompatProvider is OpenAIProvider


def test_provider_class_is_importable():
    """Both names can be imported."""
    assert OpenAIProvider is not None
    assert OpenAICompatProvider is not None

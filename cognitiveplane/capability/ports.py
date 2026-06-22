"""Capability plane — port ABC (spec §骨架 line 963).

Canonical home for `LLMProvider`. The concrete request/response models
live in `capability.provider` (spec §骨架 line 964); this module
exposes only the ABC so adapters depend on the contract, not on the
DTO shapes that may evolve.
"""

from cognitiveplane.capability.provider import LLMProvider

__all__ = ["LLMProvider"]

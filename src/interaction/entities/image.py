"""ImageEntity — represents a single inspection image.

Source: L1-Interaction-Layer-Business-Requirements.md §2.6.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.shared.types import CaseId, ImageId


class ImageEntity(BaseModel):
    """A single inspection image with metadata and CP status."""

    image_id: ImageId
    case_id: CaseId
    image_index: int = Field(ge=0)
    image_path: str = ""
    cp_status: str | None = None
    inspection_result: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] = []

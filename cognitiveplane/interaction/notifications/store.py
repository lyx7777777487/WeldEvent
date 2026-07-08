"""Notification store — stores pending notifications for real-time push.

Provides in-memory storage and pub/sub for notifications.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4, UUID


class NotificationType(str, Enum):
    """Types of notifications."""
    CONFIRMATION_REQUEST = "confirmation_request"
    ESCALATION = "escalation"
    ALERT = "alert"
    INSTRUCTION = "instruction"
    WORKFLOW_UPDATE = "workflow_update"


class NotificationStatus(str, Enum):
    """Status of a notification."""
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass
class Notification:
    """A notification sent to the operator."""
    notification_type: NotificationType
    title: str
    message: str
    notification_id: UUID = field(default_factory=uuid4)
    status: NotificationStatus = NotificationStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    operator_id: str = "operator-001"

    def model_dump(self, mode: str = "json") -> dict:
        """Serialize to dict."""
        return {
            "notification_id": str(self.notification_id),
            "notification_type": self.notification_type.value,
            "title": self.title,
            "message": self.message,
            "status": self.status.value,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "operator_id": self.operator_id,
        }


class NotificationStore:
    """In-memory notification store with pub/sub support."""

    def __init__(self) -> None:
        self._notifications: dict[UUID, Notification] = {}
        self._subscribers: set[asyncio.Queue[Notification]] = set()
        self._lock = asyncio.Lock()

    async def add(self, notification: Notification) -> Notification:
        """Add a notification and publish to subscribers."""
        async with self._lock:
            self._notifications[notification.notification_id] = notification
            # Publish to all subscribers
            for queue in self._subscribers:
                await queue.put(notification)
            return notification

    async def get(self, notification_id: UUID) -> Notification | None:
        """Get a notification by ID."""
        async with self._lock:
            return self._notifications.get(notification_id)

    async def get_pending(self, operator_id: str = "operator-001") -> list[Notification]:
        """Get all pending notifications for an operator."""
        async with self._lock:
            return [
                n for n in self._notifications.values()
                if n.operator_id == operator_id
                and n.status == NotificationStatus.PENDING
            ]

    async def update_status(
        self, notification_id: UUID, status: NotificationStatus
    ) -> Notification | None:
        """Update notification status."""
        async with self._lock:
            notification = self._notifications.get(notification_id)
            if notification:
                notification.status = status
                # Publish update
                for queue in self._subscribers:
                    await queue.put(notification)
            return notification

    async def subscribe(self) -> asyncio.Queue[Notification]:
        """Subscribe to notifications."""
        queue: asyncio.Queue[Notification] = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Notification]) -> None:
        """Unsubscribe from notifications."""
        async with self._lock:
            self._subscribers.discard(queue)

    async def cleanup_expired(self) -> int:
        """Remove expired notifications."""
        now = datetime.now(timezone.utc)
        removed = 0
        async with self._lock:
            to_remove = [
                nid for nid, n in self._notifications.items()
                if n.expires_at and n.expires_at < now
            ]
            for nid in to_remove:
                del self._notifications[nid]
                removed += 1
        return removed


# Singleton instance
_notification_store: NotificationStore | None = None


def get_notification_store() -> NotificationStore:
    """Get the singleton notification store."""
    global _notification_store
    if _notification_store is None:
        _notification_store = NotificationStore()
    return _notification_store

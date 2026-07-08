"""FastAPI notifications router — GET /notifications and WebSocket push.

Source: 7-plane redesign spec §7 lines 1460-1471.
"""

import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from uuid import UUID

from cognitiveplane.interaction.notifications.store import (
    get_notification_store,
    NotificationStatus,
)


def create_notifications_router() -> APIRouter:
    """Create FastAPI router for notification endpoints."""
    router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

    @router.get("/")
    async def get_notifications(operator_id: str = "operator-001") -> dict:
        """Poll pending confirmations/alerts for an operator."""
        store = get_notification_store()
        pending = await store.get_pending(operator_id)
        return {
            "notifications": [n.model_dump() for n in pending],
            "operator_id": operator_id,
        }

    @router.post("/{notification_id}/acknowledge")
    async def acknowledge_notification(notification_id: str) -> dict:
        """Acknowledge a notification."""
        store = get_notification_store()
        try:
            nid = UUID(notification_id)
            notification = await store.update_status(nid, NotificationStatus.ACKNOWLEDGED)
            if notification:
                return {"success": True, "notification_id": notification_id}
            return {"success": False, "error": "Notification not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.post("/{notification_id}/resolve")
    async def resolve_notification(notification_id: str, decision: str = "approved") -> dict:
        """Resolve a notification (approve or reject)."""
        store = get_notification_store()
        try:
            nid = UUID(notification_id)
            notification = await store.update_status(nid, NotificationStatus.RESOLVED)
            if notification:
                return {"success": True, "notification_id": notification_id, "decision": decision}
            return {"success": False, "error": "Notification not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.websocket("/ws")
    async def websocket_notifications(websocket: WebSocket) -> None:
        """Real-time push notifications for operator."""
        await websocket.accept()
        store = get_notification_store()
        queue = await store.subscribe()

        try:
            # Send existing pending notifications first
            pending = await store.get_pending()
            for notification in pending:
                await websocket.send_json(notification.model_dump())

            # Listen for new notifications
            while True:
                try:
                    notification = await queue.get()
                    await websocket.send_json(notification.model_dump())
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await store.unsubscribe(queue)

    return router

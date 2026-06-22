"""FastAPI notifications router — GET /notifications and WebSocket push.

Source: 7-plane redesign spec §7 lines 1460-1471.
"""

from fastapi import APIRouter, WebSocket


def create_notifications_router() -> APIRouter:
    """Create FastAPI router for notification endpoints."""
    router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

    @router.get("/")
    async def get_notifications(operator_id: str = "operator-001") -> dict:
        """Poll pending confirmations/alerts for an operator."""
        return {"notifications": [], "operator_id": operator_id}

    @router.websocket("/ws")
    async def websocket_notifications(websocket: WebSocket) -> None:
        """Real-time push notifications for operator."""
        await websocket.accept()
        # Stub: in full implementation, subscribes to event bus
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass

    return router

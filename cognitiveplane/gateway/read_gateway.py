"""WeldMapReadGateway — implements CognitiveGatewayReadPort.

Delegates to WeldMapHTTPClient for actual reads from WeldMap Store.
"""

from cognitiveplane.gateway.ports import CognitiveGatewayReadPort
from cognitiveplane.gateway.weldmap_client import WeldMapHTTPClient
from cognitiveplane.shared.dto.context import WeldMapSnapshot
from cognitiveplane.shared.dto.gateway import CaseData, WorkflowState
from cognitiveplane.shared.types import CaseId


class WeldMapReadGateway(CognitiveGatewayReadPort):
    """Read gateway delegating to WeldMapHTTPClient."""

    def __init__(self, client: WeldMapHTTPClient) -> None:
        self._client = client

    async def read_weldmap_snapshot(self, case_id: CaseId) -> WeldMapSnapshot:
        from datetime import datetime, timezone
        data = await self._client.read("snapshot", str(case_id.value))
        if data:
            return WeldMapSnapshot.model_validate(data)
        return WeldMapSnapshot(
            case_id=case_id,
            workflow_state={},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=datetime.now(timezone.utc),
        )

    async def read_workflow_state(self, case_id: CaseId) -> WorkflowState | None:
        from datetime import datetime, timezone
        data = await self._client.read("workflow", str(case_id.value))
        if data:
            return WorkflowState.model_validate(data)
        return None

    async def read_case_data(self, case_id: CaseId) -> CaseData | None:
        from datetime import datetime, timezone
        data = await self._client.read("case", str(case_id.value))
        if data:
            return CaseData.model_validate(data)
        return None

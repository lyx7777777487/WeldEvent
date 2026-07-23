"""Plan versioning - Op 16/17/18/19.

Op 16: PlanVersion - immutable plan snapshot (调研报告 §2 + Temporal determinism)
Op 17: ContextManifest - frozen model version + context hash (调研报告 §5.4)
Op 18: PlanRevisionProposal - sole channel for plan changes with impact analysis (§7.2)
Op 19: Cancel + Relaunch - Temporal workflow versioning + Update API (1.25+)
"""

from __future__ import annotations

import hashlib
import json
import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanVersionState(str, Enum):
    """Op 16: Plan version lifecycle."""
    DRAFT = "draft"
    FROZEN = "frozen"       # immutable snapshot, ready for execution
    SUPERSEDED = "superseded"  # replaced by a newer version
    CANCELLED = "cancelled"    # cancelled by user


@dataclass(frozen=True)
class PlanVersion:
    """Op 16: Immutable plan version.

    Source: 调研报告分层纪律 §2 + Temporal workflow determinism.

    Temporal requires workflow determinism; immutable specs prevent
    replay inconsistency. Once a version is FROZEN, it cannot be modified.
    Changes require a new PlanVersion (via PlanRevisionProposal).
    """
    version_id: str
    parent_version_id: str | None  # None for initial version
    spec_dict: dict[str, Any]      # frozen WorkflowSpec dict
    state: PlanVersionState
    created_at: float = 0.0  # 0.0 default; caller sets via create_version(timestamp=)
    frozen_at: float | None = None
    # Op 17: Context manifest (frozen model version + context hash)
    manifest: "ContextManifest | None" = None

    def freeze(self, timestamp: float = 0.0) -> "PlanVersion":
        """Freeze this version (returns a new frozen instance).

        Args:
            timestamp: Optional timestamp (Temporal sandbox: pass workflow.now().timestamp()).
                        0.0 = caller sets later.
        """
        return PlanVersion(
            version_id=self.version_id,
            parent_version_id=self.parent_version_id,
            spec_dict=self.spec_dict,
            state=PlanVersionState.FROZEN,
            created_at=self.created_at,
            frozen_at=timestamp,
            manifest=self.manifest,
        )

    def supersede(self) -> "PlanVersion":
        """Mark this version as superseded."""
        return PlanVersion(
            version_id=self.version_id,
            parent_version_id=self.parent_version_id,
            spec_dict=self.spec_dict,
            state=PlanVersionState.SUPERSEDED,
            created_at=self.created_at,
            frozen_at=self.frozen_at,
            manifest=self.manifest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "parent_version_id": self.parent_version_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "frozen_at": self.frozen_at,
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "spec_summary": {
                "workflow_id": self.spec_dict.get("workflow_id"),
                "objective": self.spec_dict.get("objective"),
                "node_count": len(self.spec_dict.get("nodes", [])),
            },
        }


@dataclass(frozen=True)
class ContextManifest:
    """Op 17: Frozen context manifest.

    Source: Pydantic AI Harness context manifest (调研报告 §5.4).

    Captures the model version and context hash at plan creation time.
    This ensures the same model is used during replay and that context
    changes are detectable.
    """
    model_id: str           # e.g. "deepseek-chat-v1"
    embedding_model: str    # e.g. "text-embedding-ada-002"
    context_hash: str       # hash of the full spec dict
    created_at: float = 0.0  # 0.0 default; caller sets via create_version(timestamp=)

    @classmethod
    def from_spec(cls, spec_dict: dict[str, Any], model_id: str = "unknown",
                   embedding_model: str = "unknown", timestamp: float = 0.0) -> "ContextManifest":
        """Create a manifest from a spec dict.

        Args:
            timestamp: Optional timestamp (Temporal sandbox: pass workflow.now().timestamp()).
        """
        spec_str = json.dumps(spec_dict, sort_keys=True, default=str)
        context_hash = hashlib.sha256(spec_str.encode()).hexdigest()[:16]
        return cls(model_id=model_id, embedding_model=embedding_model,
                   context_hash=context_hash, created_at=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "embedding_model": self.embedding_model,
            "context_hash": self.context_hash,
            "created_at": self.created_at,
        }


class RevisionType(str, Enum):
    """Op 18: Types of plan revisions."""
    ADD_NODE = "add_node"
    REMOVE_NODE = "remove_node"
    MODIFY_NODE = "modify_node"
    MODIFY_PARAMS = "modify_params"   # parameter-only change (no topology change)
    REORDER = "reorder"


@dataclass
class PlanRevisionProposal:
    """Op 18: Sole channel for plan modifications.

    Source: 调研报告 §7.2.

    Any plan change must go through this proposal with:
    - impact analysis (which nodes are affected)
    - revision type (topology change vs parameter change)
    - approval status

    Topology changes (add/remove/reorder) require Cancel + Relaunch (Op 19).
    Parameter-only changes can use Temporal Update API (no cancel needed).
    """
    proposal_id: str
    target_version_id: str
    revision_type: RevisionType
    change_description: str
    impact_nodes: list[str]       # affected node IDs
    new_spec_dict: dict[str, Any] | None = None  # full replacement spec
    param_changes: dict[str, Any] | None = None  # parameter-only changes
    approved: bool = False
    approved_by: str | None = None
    created_at: float = 0.0  # 0.0 default; caller sets via create_version(timestamp=)

    @property
    def requires_cancel_relaunch(self) -> bool:
        """Op 19: Topology changes require Cancel + Relaunch.

        Source: Temporal workflow versioning best practice + Update API (1.25+).
        - Topology change (add/remove/reorder) -> Cancel + Relaunch
        - Parameter-only change -> Update API (no cancel)
        """
        return self.revision_type in (
            RevisionType.ADD_NODE,
            RevisionType.REMOVE_NODE,
            RevisionType.REORDER,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "target_version_id": self.target_version_id,
            "revision_type": self.revision_type.value,
            "change_description": self.change_description,
            "impact_nodes": self.impact_nodes,
            "requires_cancel_relaunch": self.requires_cancel_relaunch,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "created_at": self.created_at,
        }


@dataclass
class CancelRelaunchResult:
    """Op 19: Result of Cancel + Relaunch operation.

    Source: Temporal workflow versioning + Update API (1.25+).

    When topology changes require a full cancel+relaunch:
    - completed_nodes: results preserved from cancelled run
    - new_workflow_id: new Temporal workflow with updated spec
    - remaining_nodes: nodes that still need execution
    """
    old_workflow_id: str
    new_workflow_id: str
    completed_node_results: dict[str, dict[str, Any]]
    remaining_nodes: list[str]
    cancel_reason: str = "topology_change"

    @property
    def preserved_count(self) -> int:
        return len(self.completed_node_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_workflow_id": self.old_workflow_id,
            "new_workflow_id": self.new_workflow_id,
            "preserved_count": self.preserved_count,
            "remaining_nodes": self.remaining_nodes,
            "cancel_reason": self.cancel_reason,
        }


class PlanVersionRegistry:
    """Op 16-19: Registry for plan versions and revisions.

    Manages the versioning lifecycle:
    1. Create initial PlanVersion (DRAFT -> FROZEN)
    2. Propose revisions via PlanRevisionProposal
    3. Apply approved revisions (Cancel+Relaunch or Update)
    4. Track version history
    """

    def __init__(self) -> None:
        self._versions: list[PlanVersion] = []
        self._proposals: list[PlanRevisionProposal] = []
        self._cancel_relaunch_results: list[CancelRelaunchResult] = []

    @property
    def current_version(self) -> PlanVersion | None:
        """Get the latest non-superseded version."""
        for v in reversed(self._versions):
            if v.state in (PlanVersionState.DRAFT, PlanVersionState.FROZEN):
                return v
        return None

    @property
    def version_history(self) -> list[PlanVersion]:
        return list(self._versions)

    def create_version(
        self,
        spec_dict: dict[str, Any],
        model_id: str = "unknown",
        embedding_model: str = "unknown",
        timestamp: float = 0.0,
    ) -> PlanVersion:
        """Create a new plan version (starts as DRAFT).

        Args:
            timestamp: Optional timestamp (Temporal sandbox: pass workflow.now().timestamp()).
        """
        parent = self.current_version
        version = PlanVersion(
            version_id=f"pv_{len(self._versions) + 1}",
            parent_version_id=parent.version_id if parent else None,
            spec_dict=spec_dict,
            state=PlanVersionState.DRAFT,
            created_at=timestamp,
            manifest=ContextManifest.from_spec(spec_dict, model_id, embedding_model, timestamp),
        )
        self._versions.append(version)
        return version

    def freeze_version(self, version_id: str, timestamp: float = 0.0) -> PlanVersion | None:
        """Freeze a draft version (make it immutable)."""
        for i, v in enumerate(self._versions):
            if v.version_id == version_id and v.state == PlanVersionState.DRAFT:
                frozen = v.freeze(timestamp)
                self._versions[i] = frozen
                return frozen
        return None

    def propose_revision(
        self,
        revision_type: RevisionType,
        change_description: str,
        impact_nodes: list[str],
        new_spec_dict: dict[str, Any] | None = None,
        param_changes: dict[str, Any] | None = None,
    ) -> PlanRevisionProposal:
        """Op 18: Create a revision proposal."""
        target = self.current_version
        if target is None:
            raise ValueError("No current version to revise")

        proposal = PlanRevisionProposal(
            proposal_id=f"rev_{len(self._proposals) + 1}",
            target_version_id=target.version_id,
            revision_type=revision_type,
            change_description=change_description,
            impact_nodes=impact_nodes,
            new_spec_dict=new_spec_dict,
            param_changes=param_changes,
        )
        self._proposals.append(proposal)
        return proposal

    def approve_proposal(self, proposal_id: str, approver: str = "user") -> PlanRevisionProposal | None:
        """Approve a revision proposal and apply it."""
        for i, p in enumerate(self._proposals):
            if p.proposal_id == proposal_id and not p.approved:
                p.approved = True
                p.approved_by = approver
                self._proposals[i] = p

                # Supersede old version
                target_idx = None
                for j, v in enumerate(self._versions):
                    if v.version_id == p.target_version_id:
                        target_idx = j
                        break
                if target_idx is not None:
                    self._versions[target_idx] = self._versions[target_idx].supersede()

                # Create new version if topology change
                if p.requires_cancel_relaunch and p.new_spec_dict:
                    new_version = self.create_version(p.new_spec_dict)
                    self.freeze_version(new_version.version_id)
                return p
        return None

    def record_cancel_relaunch(
        self,
        old_workflow_id: str,
        new_workflow_id: str,
        completed_results: dict[str, dict[str, Any]],
        remaining_nodes: list[str],
    ) -> CancelRelaunchResult:
        """Op 19: Record a Cancel + Relaunch operation."""
        result = CancelRelaunchResult(
            old_workflow_id=old_workflow_id,
            new_workflow_id=new_workflow_id,
            completed_node_results=completed_results,
            remaining_nodes=remaining_nodes,
        )
        self._cancel_relaunch_results.append(result)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "versions": [v.to_dict() for v in self._versions],
            "proposals": [p.to_dict() for p in self._proposals],
            "cancel_relaunch_results": [r.to_dict() for r in self._cancel_relaunch_results],
            "current_version": self.current_version.to_dict() if self.current_version else None,
        }


__all__ = [
    "PlanVersionState",
    "PlanVersion",
    "ContextManifest",
    "RevisionType",
    "PlanRevisionProposal",
    "CancelRelaunchResult",
    "PlanVersionRegistry",
]

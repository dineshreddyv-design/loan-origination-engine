"""Stage machine with an append-only audit trail.

Illegal transitions raise instead of silently corrupting the pipeline -- an
origination system that lets a file jump from APPLICATION to FUNDED is a
compliance incident waiting to happen.
"""
from datetime import datetime, timezone
from typing import Dict, List, Set

from .models import Application, Stage

TRANSITIONS: Dict[Stage, Set[Stage]] = {
    Stage.APPLICATION: {Stage.DOCUMENT_COLLECTION, Stage.WITHDRAWN, Stage.DECLINED},
    Stage.DOCUMENT_COLLECTION: {Stage.UNDERWRITING, Stage.WITHDRAWN, Stage.DECLINED},
    Stage.UNDERWRITING: {Stage.CONDITIONAL_APPROVAL, Stage.CLEAR_TO_CLOSE,
                         Stage.DECLINED, Stage.WITHDRAWN},
    Stage.CONDITIONAL_APPROVAL: {Stage.CLEAR_TO_CLOSE, Stage.UNDERWRITING,
                                 Stage.DECLINED, Stage.WITHDRAWN},
    Stage.CLEAR_TO_CLOSE: {Stage.FUNDED, Stage.WITHDRAWN},
    Stage.FUNDED: set(),
    Stage.DECLINED: set(),
    Stage.WITHDRAWN: set(),
}


class IllegalTransition(Exception):
    pass


class Pipeline:
    def __init__(self):
        self.audit: List[dict] = []

    def can_move(self, current: Stage, target: Stage) -> bool:
        return target in TRANSITIONS[current]

    def move(self, application: Application, target: Stage,
             actor: str = "system", reason: str = "") -> Application:
        if not self.can_move(application.stage, target):
            raise IllegalTransition(
                f"{application.stage.value} -> {target.value} is not permitted"
            )
        entry = {
            "application_id": application.application_id,
            "from": application.stage.value,
            "to": target.value,
            "actor": actor,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        application.stage = target
        self.audit.append(entry)
        return application

    def history(self, application_id: str) -> List[dict]:
        return [e for e in self.audit if e["application_id"] == application_id]

    def cycle_time_days(self, application_id: str) -> float:
        events = self.history(application_id)
        if len(events) < 2:
            return 0.0
        start = datetime.fromisoformat(events[0]["at"])
        end = datetime.fromisoformat(events[-1]["at"])
        return round((end - start).total_seconds() / 86400, 4)

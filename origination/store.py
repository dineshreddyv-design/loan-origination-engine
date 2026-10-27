"""SQLite persistence. Decisions are stored immutably: a re-decision writes a
new row rather than updating the old one, so the full credit history of a file
survives audit."""
import json
import sqlite3
from typing import List, Optional

from .models import (Application, Borrower, Decision, LoanPurpose, Outcome,
                     Property, Stage)

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY,
    payload        TEXT NOT NULL,
    stage          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    risk_tier      TEXT NOT NULL,
    priced_rate    REAL NOT NULL,
    payload        TEXT NOT NULL,
    decided_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decisions_app ON decisions(application_id);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- applications ---------------------------------------------------
    def save(self, app: Application) -> None:
        payload = {
            "borrower": app.borrower.__dict__,
            "property": app.property.__dict__,
            "loan_amount": app.loan_amount,
            "purpose": app.purpose.value,
            "term_months": app.term_months,
            "submitted_on": app.submitted_on.isoformat(),
            "documents": app.documents,
        }
        self.conn.execute(
            "INSERT INTO applications(application_id, payload, stage) VALUES (?,?,?) "
            "ON CONFLICT(application_id) DO UPDATE SET payload=excluded.payload, "
            "stage=excluded.stage",
            (app.application_id, json.dumps(payload), app.stage.value),
        )
        self.conn.commit()

    def get(self, application_id: str) -> Optional[Application]:
        row = self.conn.execute(
            "SELECT * FROM applications WHERE application_id=?", (application_id,)
        ).fetchone()
        if row is None:
            return None
        p = json.loads(row["payload"])
        from datetime import date
        return Application(
            application_id=row["application_id"],
            borrower=Borrower(**p["borrower"]),
            property=Property(**p["property"]),
            loan_amount=p["loan_amount"],
            purpose=LoanPurpose(p["purpose"]),
            term_months=p["term_months"],
            submitted_on=date.fromisoformat(p["submitted_on"]),
            stage=Stage(row["stage"]),
            documents=p["documents"],
        )

    # -- decisions ------------------------------------------------------
    def record_decision(self, decision: Decision) -> None:
        self.conn.execute(
            "INSERT INTO decisions(application_id, outcome, risk_tier, priced_rate, "
            "payload) VALUES (?,?,?,?,?)",
            (decision.application_id, decision.outcome.value, decision.risk_tier,
             decision.priced_rate, json.dumps(decision.to_dict())),
        )
        self.conn.commit()

    def decision_history(self, application_id: str) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM decisions WHERE application_id=? ORDER BY id", (application_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def funnel(self) -> dict:
        rows = self.conn.execute(
            "SELECT stage, COUNT(*) c FROM applications GROUP BY stage"
        ).fetchall()
        return {r["stage"]: r["c"] for r in rows}

    def close(self):
        self.conn.close()

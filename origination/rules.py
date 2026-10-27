"""Credit policy: risk-based pricing plus a declarative underwriting rule set.

Every rule is a pure function of the application, which keeps the policy
auditable and makes each rule independently testable -- the same property
regulators look for when they ask "why was this applicant declined?".
"""
from typing import List

from .models import Application, Decision, Outcome, RuleResult

BASE_RATE = 6.25

# (min_score, tier, rate adjustment in percentage points)
PRICING_GRID = [
    (780, "A+", -0.375),
    (740, "A", -0.125),
    (700, "B", 0.125),
    (660, "C", 0.500),
    (620, "D", 1.125),
    (0,   "E", 2.250),
]

MAX_LTV = {"PRIMARY": 97.0, "SECOND_HOME": 90.0, "INVESTMENT": 80.0}
MAX_DTI = 43.0
REFER_DTI = 38.0
MIN_SCORE = 620


def price(application: Application):
    """Return (risk_tier, rate) using the credit-score grid plus LLPAs."""
    score = application.borrower.credit_score
    tier, adjustment = next(
        (t, adj) for floor, t, adj in PRICING_GRID if score >= floor
    )
    rate = BASE_RATE + adjustment

    # Loan-level price adjustments
    if application.property.occupancy == "INVESTMENT":
        rate += 0.750
    elif application.property.occupancy == "SECOND_HOME":
        rate += 0.250
    if application.ltv > 80:
        rate += 0.250
    if application.borrower.self_employed:
        rate += 0.125
    return tier, round(rate, 3)


# --- individual rules --------------------------------------------------------

def _rule_credit_score(app: Application, rate: float) -> RuleResult:
    ok = app.borrower.credit_score >= MIN_SCORE
    return RuleResult("CREDIT_SCORE", ok,
                      f"Score {app.borrower.credit_score} vs minimum {MIN_SCORE}")


def _rule_ltv(app: Application, rate: float) -> RuleResult:
    cap = MAX_LTV[app.property.occupancy]
    return RuleResult("LTV", app.ltv <= cap,
                      f"LTV {app.ltv}% vs cap {cap}% for {app.property.occupancy}")


def _rule_dti(app: Application, rate: float) -> RuleResult:
    dti = app.dti(rate)
    if dti <= REFER_DTI:
        return RuleResult("DTI", True, f"DTI {dti}% within {REFER_DTI}%")
    if dti <= MAX_DTI:
        return RuleResult("DTI", False, f"DTI {dti}% needs manual review", "SOFT")
    return RuleResult("DTI", False, f"DTI {dti}% exceeds {MAX_DTI}%")


def _rule_employment(app: Application, rate: float) -> RuleResult:
    needed = 24 if app.borrower.self_employed else 6
    months = app.borrower.employment_months
    return RuleResult("EMPLOYMENT", months >= needed,
                      f"{months} months of history vs {needed} required",
                      "SOFT" if months >= needed / 2 else "HARD")


def _rule_documents(app: Application, rate: float) -> RuleResult:
    missing = app.missing_documents()
    return RuleResult("DOCUMENTS", not missing,
                      "Complete" if not missing else f"Missing: {', '.join(missing)}",
                      "SOFT")


RULES = [_rule_credit_score, _rule_ltv, _rule_dti, _rule_employment, _rule_documents]


def evaluate(application: Application) -> Decision:
    """Run the full policy and return an auditable decision."""
    tier, rate = price(application)
    results: List[RuleResult] = [rule(application, rate) for rule in RULES]

    hard_fails = [r for r in results if not r.passed and r.severity == "HARD"]
    soft_fails = [r for r in results if not r.passed and r.severity == "SOFT"]

    if hard_fails:
        outcome = Outcome.DECLINE
    elif soft_fails:
        outcome = Outcome.REFER
    else:
        outcome = Outcome.APPROVE

    conditions = []
    if outcome != Outcome.DECLINE:
        if application.ltv > 80:
            conditions.append("Private mortgage insurance required (LTV above 80%)")
        for r in soft_fails:
            conditions.append(f"Clear condition: {r.message}")

    return Decision(application.application_id, outcome, tier, rate, results, conditions)

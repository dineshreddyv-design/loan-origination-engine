# Loan Origination Decision Engine

A mortgage origination back end: intake, risk-based pricing, an auditable
underwriting rule set, and a stage machine that will not let a file skip
compliance steps.

**Sector:** Banking — Origination
**Stack:** Python 3.9+ standard library only (`sqlite3`, `dataclasses`, `unittest`)

---

## The problem

Origination systems fail in two expensive ways. They approve loans they should
not, and they cannot explain the loans they declined. Under ECOA/Reg B a lender
must give an applicant the specific principal reasons for an adverse action —
"the model said no" is not a defence.

This engine is built so that every decision decomposes into named rules, each
with the value it tested and the threshold it tested against.

## Design decisions

**Rules are pure functions.** Each rule takes the application and returns a
`RuleResult` with a code, a pass/fail, and a human-readable message. No shared
state, no ordering dependency. Adding a policy rule means adding a function to
one list — and it is independently unit-testable, which is what makes a credit
policy defensible.

**Hard vs. soft failures.** A credit score below the floor is a hard decline. An
incomplete document package is a *referral* — the file is fine, the paperwork is
not. Collapsing these into a single boolean is how lenders end up declining
creditworthy applicants over a missing W-2.

**Pricing is a grid, not a formula.** Credit tier sets the base adjustment;
loan-level price adjustments stack on top for occupancy, LTV, and self-employment.
This mirrors how agency pricing actually works and keeps the grid reviewable by
someone who is not an engineer.

**Illegal transitions raise.** `APPLICATION → FUNDED` throws `IllegalTransition`.
The audit trail is append-only and records actor, reason, and timestamp on every
move.

**Decisions are append-only.** Re-underwriting a file writes a new decision row.
The prior decision is never mutated, so the credit history of a file survives audit.

## Layout

```
origination/
    models.py     Borrower, Property, Application; LTV / DTI / payment maths
    rules.py      Pricing grid + the five-rule credit policy
    workflow.py   Stage machine and append-only audit trail
    store.py      SQLite persistence
tests/
    test_origination.py   32 tests
demo.py           Three files, three outcomes, end to end
```

## Run it

```bash
python3 demo.py
python3 -m unittest discover -s tests -v
```

No installation, no dependencies.

## Worked example

```
APP-1002  Thin file, docs outstanding
  LTV 85.0%   DTI 35.34%   P&I $2,177.06
  -> REFER | tier B @ 6.625%
     [PASS] CREDIT_SCORE Score 705 vs minimum 620
     [PASS] LTV          LTV 85.0% vs cap 97.0% for PRIMARY
     [SOFT] DOCUMENTS    Missing: W2, BANK_STATEMENT, PURCHASE_AGREEMENT
     * Private mortgage insurance required (LTV above 80%)
```

## What I would add next

- Automated Underwriting System (AUS) findings ingestion — DU/LP response parsing
- Fair-lending monitoring: disparate impact testing across the decision set
- Rule versioning, so a decision replays against the policy in force on its date
- Conditions engine with per-condition ownership and SLA tracking

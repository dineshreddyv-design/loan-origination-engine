"""End-to-end walkthrough: three files, three different credit outcomes."""
from origination.models import Application, Borrower, LoanPurpose, Property, Stage
from origination.rules import evaluate
from origination.store import Store
from origination.workflow import Pipeline

FULL_DOCS = ["ID", "PAYSTUB", "W2", "BANK_STATEMENT", "PURCHASE_AGREEMENT"]

FILES = [
    ("APP-1001", "Prime purchase", 780, 190_000, 800, 48, 400_000, 600_000, FULL_DOCS),
    ("APP-1002", "Thin file, docs outstanding", 705, 96_000, 650, 9, 340_000, 400_000, ["ID", "PAYSTUB"]),
    ("APP-1003", "Over-levered", 590, 62_000, 1_900, 30, 300_000, 330_000, FULL_DOCS),
]


def main():
    store, pipeline = Store(), Pipeline()

    for (aid, label, score, income, debt, months,
         loan, value, docs) in FILES:
        app = Application(
            application_id=aid,
            borrower=Borrower(aid + "-B", label, score, income, debt, months),
            property=Property("123 Main St", value),
            loan_amount=loan, purpose=LoanPurpose.PURCHASE, documents=docs,
        )
        store.save(app)
        pipeline.move(app, Stage.DOCUMENT_COLLECTION, actor="loan_officer")
        pipeline.move(app, Stage.UNDERWRITING, actor="loan_officer")

        decision = evaluate(app)
        store.record_decision(decision)

        print(f"\n{aid}  {label}")
        print(f"  LTV {app.ltv}%   DTI {app.dti(decision.priced_rate)}%   "
              f"P&I ${app.estimated_payment(decision.priced_rate):,.2f}")
        print(f"  -> {decision.outcome.value} | tier {decision.risk_tier} "
              f"@ {decision.priced_rate}%")
        for r in decision.results:
            print(f"     [{'PASS' if r.passed else r.severity:>4}] {r.code:<12} {r.message}")
        for c in decision.conditions:
            print(f"     * {c}")

        target = {"APPROVE": Stage.CLEAR_TO_CLOSE,
                  "REFER": Stage.CONDITIONAL_APPROVAL,
                  "DECLINE": Stage.DECLINED}[decision.outcome.value]
        pipeline.move(app, target, actor="underwriter", reason=decision.outcome.value)
        store.save(app)

    print("\nPipeline funnel:", store.funnel())
    store.close()


if __name__ == "__main__":
    main()

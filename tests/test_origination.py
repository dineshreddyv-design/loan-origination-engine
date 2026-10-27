import unittest
from datetime import date

from origination.models import (Application, Borrower, LoanPurpose, Outcome,
                                Property, Stage)
from origination.rules import evaluate, price
from origination.store import Store
from origination.workflow import IllegalTransition, Pipeline


def build(**overrides) -> Application:
    borrower = Borrower(
        borrower_id="B1", full_name="Test Borrower",
        credit_score=overrides.pop("credit_score", 760),
        annual_income=overrides.pop("annual_income", 180_000),
        monthly_debt=overrides.pop("monthly_debt", 900),
        employment_months=overrides.pop("employment_months", 48),
        self_employed=overrides.pop("self_employed", False),
    )
    prop = Property(
        address="1 Example Way",
        appraised_value=overrides.pop("appraised_value", 500_000),
        occupancy=overrides.pop("occupancy", "PRIMARY"),
    )
    return Application(
        application_id=overrides.pop("application_id", "APP-1"),
        borrower=borrower, property=prop,
        loan_amount=overrides.pop("loan_amount", 350_000),
        purpose=overrides.pop("purpose", LoanPurpose.PURCHASE),
        documents=overrides.pop("documents",
                                ["ID", "PAYSTUB", "W2", "BANK_STATEMENT",
                                 "PURCHASE_AGREEMENT"]),
    )


class TestRatios(unittest.TestCase):
    def test_ltv(self):
        self.assertEqual(build().ltv, 70.0)

    def test_zero_appraisal_rejected(self):
        with self.assertRaises(ValueError):
            build(appraised_value=0).ltv

    def test_payment_amortises(self):
        pmt = build().estimated_payment(6.0)
        self.assertAlmostEqual(pmt, 2098.43, places=2)

    def test_zero_rate_is_straight_line(self):
        self.assertEqual(build().estimated_payment(0), round(350_000 / 360, 2))

    def test_dti_includes_new_payment(self):
        app = build()
        self.assertGreater(app.dti(6.0), app.borrower.monthly_debt / app.borrower.monthly_income * 100)

    def test_missing_documents(self):
        app = build(documents=["ID", "PAYSTUB"])
        self.assertEqual(sorted(app.missing_documents()),
                         ["BANK_STATEMENT", "PURCHASE_AGREEMENT", "W2"])

    def test_cash_out_needs_appraisal(self):
        app = build(purpose=LoanPurpose.CASH_OUT_REFINANCE, documents=["ID"])
        self.assertIn("APPRAISAL", app.missing_documents())


class TestPricing(unittest.TestCase):
    def test_top_tier_gets_discount(self):
        tier, rate = price(build(credit_score=800))
        self.assertEqual(tier, "A+")
        self.assertLess(rate, 6.25)

    def test_thin_credit_pays_more(self):
        _, good = price(build(credit_score=800))
        _, weak = price(build(credit_score=640))
        self.assertGreater(weak, good)

    def test_investment_property_surcharge(self):
        _, primary = price(build())
        _, investment = price(build(occupancy="INVESTMENT", loan_amount=300_000))
        self.assertAlmostEqual(investment - primary, 0.75, places=3)

    def test_high_ltv_surcharge(self):
        _, low = price(build(loan_amount=350_000))
        _, high = price(build(loan_amount=450_000))
        self.assertGreater(high, low)

    def test_self_employed_surcharge(self):
        _, w2 = price(build())
        _, se = price(build(self_employed=True))
        self.assertGreater(se, w2)


class TestDecisioning(unittest.TestCase):
    def test_clean_file_approves(self):
        d = evaluate(build())
        self.assertEqual(d.outcome, Outcome.APPROVE)
        self.assertEqual(d.failed_codes, [])

    def test_subprime_score_declines(self):
        d = evaluate(build(credit_score=580))
        self.assertEqual(d.outcome, Outcome.DECLINE)
        self.assertIn("CREDIT_SCORE", d.failed_codes)

    def test_ltv_over_cap_declines(self):
        d = evaluate(build(occupancy="INVESTMENT", loan_amount=450_000))
        self.assertEqual(d.outcome, Outcome.DECLINE)
        self.assertIn("LTV", d.failed_codes)

    def test_excessive_dti_declines(self):
        d = evaluate(build(annual_income=60_000, monthly_debt=1_500))
        self.assertEqual(d.outcome, Outcome.DECLINE)
        self.assertIn("DTI", d.failed_codes)

    def test_borderline_dti_refers(self):
        d = evaluate(build(annual_income=73_000, monthly_debt=300))
        self.assertEqual(d.outcome, Outcome.REFER)

    def test_missing_docs_refer_not_decline(self):
        d = evaluate(build(documents=["ID"]))
        self.assertEqual(d.outcome, Outcome.REFER)
        self.assertIn("DOCUMENTS", d.failed_codes)

    def test_pmi_condition_added_above_80_ltv(self):
        d = evaluate(build(loan_amount=450_000))
        self.assertTrue(any("mortgage insurance" in c for c in d.conditions))

    def test_decline_carries_every_reason(self):
        d = evaluate(build(credit_score=560, annual_income=40_000, monthly_debt=2_000))
        self.assertGreaterEqual(len(d.failed_codes), 2)

    def test_every_rule_reports(self):
        self.assertEqual(len(evaluate(build()).results), 5)


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline()
        self.app = build()

    def test_happy_path(self):
        for stage in (Stage.DOCUMENT_COLLECTION, Stage.UNDERWRITING,
                      Stage.CLEAR_TO_CLOSE, Stage.FUNDED):
            self.pipeline.move(self.app, stage, actor="uw1")
        self.assertEqual(self.app.stage, Stage.FUNDED)

    def test_cannot_skip_to_funded(self):
        with self.assertRaises(IllegalTransition):
            self.pipeline.move(self.app, Stage.FUNDED)

    def test_terminal_stage_is_terminal(self):
        self.pipeline.move(self.app, Stage.DECLINED, reason="policy")
        with self.assertRaises(IllegalTransition):
            self.pipeline.move(self.app, Stage.UNDERWRITING)

    def test_conditional_approval_can_loop_back(self):
        self.pipeline.move(self.app, Stage.DOCUMENT_COLLECTION)
        self.pipeline.move(self.app, Stage.UNDERWRITING)
        self.pipeline.move(self.app, Stage.CONDITIONAL_APPROVAL)
        self.pipeline.move(self.app, Stage.UNDERWRITING, reason="new income doc")
        self.assertEqual(self.app.stage, Stage.UNDERWRITING)

    def test_audit_trail_records_actor_and_reason(self):
        self.pipeline.move(self.app, Stage.DECLINED, actor="uw7", reason="policy")
        entry = self.pipeline.history("APP-1")[0]
        self.assertEqual(entry["actor"], "uw7")
        self.assertEqual(entry["reason"], "policy")

    def test_history_is_scoped_per_application(self):
        other = build(application_id="APP-2")
        self.pipeline.move(self.app, Stage.DOCUMENT_COLLECTION)
        self.pipeline.move(other, Stage.WITHDRAWN)
        self.assertEqual(len(self.pipeline.history("APP-1")), 1)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def tearDown(self):
        self.store.close()

    def test_round_trip(self):
        app = build()
        self.store.save(app)
        loaded = self.store.get("APP-1")
        self.assertEqual(loaded.loan_amount, app.loan_amount)
        self.assertEqual(loaded.borrower.credit_score, 760)
        self.assertEqual(loaded.ltv, app.ltv)

    def test_missing_returns_none(self):
        self.assertIsNone(self.store.get("NOPE"))

    def test_save_is_idempotent_upsert(self):
        app = build()
        self.store.save(app)
        app.stage = Stage.UNDERWRITING
        self.store.save(app)
        self.assertEqual(self.store.get("APP-1").stage, Stage.UNDERWRITING)

    def test_decisions_are_append_only(self):
        app = build()
        self.store.save(app)
        self.store.record_decision(evaluate(app))
        self.store.record_decision(evaluate(build(credit_score=600)))
        self.assertEqual(len(self.store.decision_history("APP-1")), 2)

    def test_funnel_counts_by_stage(self):
        self.store.save(build(application_id="A"))
        b = build(application_id="B")
        b.stage = Stage.FUNDED
        self.store.save(b)
        self.assertEqual(self.store.funnel(),
                         {"APPLICATION": 1, "FUNDED": 1})


if __name__ == "__main__":
    unittest.main()

"""Domain models for the loan origination engine."""
from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


class Stage(str, Enum):
    """Lifecycle stages an application moves through."""
    APPLICATION = "APPLICATION"
    DOCUMENT_COLLECTION = "DOCUMENT_COLLECTION"
    UNDERWRITING = "UNDERWRITING"
    CONDITIONAL_APPROVAL = "CONDITIONAL_APPROVAL"
    CLEAR_TO_CLOSE = "CLEAR_TO_CLOSE"
    FUNDED = "FUNDED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    REFER = "REFER"
    DECLINE = "DECLINE"


class LoanPurpose(str, Enum):
    PURCHASE = "PURCHASE"
    REFINANCE = "REFINANCE"
    CASH_OUT_REFINANCE = "CASH_OUT_REFINANCE"


REQUIRED_DOCUMENTS = {
    LoanPurpose.PURCHASE: ["ID", "PAYSTUB", "W2", "BANK_STATEMENT", "PURCHASE_AGREEMENT"],
    LoanPurpose.REFINANCE: ["ID", "PAYSTUB", "W2", "BANK_STATEMENT", "MORTGAGE_STATEMENT"],
    LoanPurpose.CASH_OUT_REFINANCE: ["ID", "PAYSTUB", "W2", "BANK_STATEMENT",
                                     "MORTGAGE_STATEMENT", "APPRAISAL"],
}


@dataclass
class Borrower:
    borrower_id: str
    full_name: str
    credit_score: int
    annual_income: float
    monthly_debt: float
    employment_months: int
    self_employed: bool = False

    @property
    def monthly_income(self) -> float:
        return round(self.annual_income / 12.0, 2)


@dataclass
class Property:
    address: str
    appraised_value: float
    occupancy: str = "PRIMARY"          # PRIMARY | SECOND_HOME | INVESTMENT
    property_type: str = "SINGLE_FAMILY"


@dataclass
class Application:
    application_id: str
    borrower: Borrower
    property: Property
    loan_amount: float
    purpose: LoanPurpose
    term_months: int = 360
    submitted_on: date = field(default_factory=date.today)
    stage: Stage = Stage.APPLICATION
    documents: List[str] = field(default_factory=list)

    # ---- derived underwriting ratios -------------------------------------
    @property
    def ltv(self) -> float:
        """Loan-to-value, as a percentage."""
        if self.property.appraised_value <= 0:
            raise ValueError("appraised_value must be positive")
        return round(self.loan_amount / self.property.appraised_value * 100, 2)

    def estimated_payment(self, annual_rate: float) -> float:
        """Standard amortising principal + interest payment."""
        r = annual_rate / 100 / 12
        n = self.term_months
        if r == 0:
            return round(self.loan_amount / n, 2)
        factor = (r * (1 + r) ** n) / ((1 + r) ** n - 1)
        return round(self.loan_amount * factor, 2)

    def dti(self, annual_rate: float) -> float:
        """Back-end debt-to-income, as a percentage."""
        income = self.borrower.monthly_income
        if income <= 0:
            raise ValueError("annual_income must be positive")
        total_debt = self.borrower.monthly_debt + self.estimated_payment(annual_rate)
        return round(total_debt / income * 100, 2)

    def missing_documents(self) -> List[str]:
        have = {d.upper() for d in self.documents}
        return [d for d in REQUIRED_DOCUMENTS[self.purpose] if d not in have]


@dataclass
class RuleResult:
    code: str
    passed: bool
    message: str
    severity: str = "HARD"   # HARD blocks approval, SOFT refers to a human


@dataclass
class Decision:
    application_id: str
    outcome: Outcome
    risk_tier: str
    priced_rate: float
    results: List[RuleResult] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)

    @property
    def failed_codes(self) -> List[str]:
        return [r.code for r in self.results if not r.passed]

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

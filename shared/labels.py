"""Scam taxonomy shared by ml/ (training) and edge/ (inference)."""
from __future__ import annotations

LABELS: list[str] = [
    "irs_gov",
    "immigration_uscis",
    "tech_support",
    "rental_deposit",
    "fake_job_check",
    "family_emergency",
    "bank_impersonation",
    "benign",
]
SCAM_LABELS: list[str] = [l for l in LABELS if l != "benign"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for l, i in LABEL2ID.items()}

PRETTY = {
    "irs_gov": "IRS / government impersonation",
    "immigration_uscis": "Immigration / visa threat",
    "tech_support": "Tech support scam",
    "rental_deposit": "Rental deposit scam",
    "fake_job_check": "Fake job / check scam",
    "family_emergency": "Family emergency scam",
    "bank_impersonation": "Bank impersonation",
    "benign": "Normal call",
}

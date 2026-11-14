"""Campus scam map: reports from students (after-call "Report" button) plus
~20 seeded demo reports around UMass Amherst. In memory; MongoDB later.

Reports never contain transcripts: type, a campus area, a coarse location and an
optional short redacted note the student chose to add.
"""
from __future__ import annotations

import random
import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field

from shared.labels import LABELS, PRETTY

# coarse campus areas (lat, lng) around UMass Amherst
AREAS = {
    "Southwest": (42.3814, -72.5300), "Central": (42.3935, -72.5218), "Northeast": (42.3948, -72.5250),
    "Orchard Hill": (42.3889, -72.5180), "Sylvan": (42.3990, -72.5206), "North Apartments": (42.3990, -72.5265),
    "Campus Center": (42.3917, -72.5268), "Isenberg": (42.3868, -72.5250), "Du Bois Library": (42.3897, -72.5283),
    "Downtown Amherst": (42.3758, -72.5197), "Puffton Village": (42.4086, -72.5263), "Brandywine": (42.4007, -72.5126),
}
SEED_NOTES = {
    "immigration_uscis": "Caller said my SEVIS record was flagged and asked for a fee in gift cards",
    "irs_gov": "Robocall about back taxes and an arrest warrant",
    "rental_deposit": "Sublet listing near campus, landlord 'out of state', wanted Zelle deposit before a tour",
    "fake_job_check": "Remote assistant job sent a check and asked me to buy equipment from their vendor",
    "bank_impersonation": "Fake fraud team asked me to read back a one-time code",
    "tech_support": "Pop-up and call saying my laptop was hacked, wanted remote access",
    "family_emergency": "Caller pretending to be my cousin needing bail money",
}


class ReportIn(BaseModel):
    user_id: Optional[str] = None
    scam_type: str
    area: str = "Campus Center"
    note: str = Field(default="", max_length=200)
    channel: str = "call"  # call | text | listing | email


class Report(ReportIn):
    id: str = Field(default_factory=lambda: "rep_" + uuid.uuid4().hex[:10])
    ts: float = Field(default_factory=time.time)
    lat: float = 0.0
    lng: float = 0.0
    seeded: bool = False


class ReportStore:
    def __init__(self, seed: bool = True, clock=time.time):
        self.clock = clock
        self.items: list[Report] = []
        if seed:
            self._seed()

    def _place(self, area: str, r: random.Random) -> tuple[float, float]:
        lat, lng = AREAS.get(area, AREAS["Campus Center"])
        return round(lat + r.uniform(-0.0025, 0.0025), 5), round(lng + r.uniform(-0.0025, 0.0025), 5)

    def _seed(self) -> None:
        r = random.Random(42)
        now = self.clock()
        weights = {"immigration_uscis": 5, "rental_deposit": 4, "irs_gov": 3, "fake_job_check": 3,
                   "bank_impersonation": 2, "tech_support": 2, "family_emergency": 1}
        for stype, n in weights.items():
            for _ in range(n):
                area = r.choice(list(AREAS))
                lat, lng = self._place(area, r)
                self.items.append(Report(scam_type=stype, area=area, note=SEED_NOTES[stype],
                                         channel="listing" if stype == "rental_deposit" else "call",
                                         ts=now - r.uniform(0.5, 7) * 86400, lat=lat, lng=lng, seeded=True))

    def add(self, rep: ReportIn) -> Report:
        if rep.scam_type not in LABELS:
            raise ValueError("unknown scam_type")
        lat, lng = self._place(rep.area, random.Random())
        item = Report(**rep.model_dump(), ts=self.clock(), lat=lat, lng=lng)
        self.items.append(item)
        return item

    def map(self, days: float = 14) -> dict:
        cutoff = self.clock() - days * 86400
        recent = [x for x in self.items if x.ts >= cutoff]
        week = self.clock() - 7 * 86400
        counts: dict[str, int] = {}
        for x in recent:
            if x.ts >= week:
                counts[x.scam_type] = counts.get(x.scam_type, 0) + 1
        return {"reports": [{**x.model_dump(exclude={"user_id"}), "label": PRETTY.get(x.scam_type, x.scam_type)} for x in recent],
                "week_counts": {PRETTY.get(k, k): v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])},
                "areas": list(AREAS)}

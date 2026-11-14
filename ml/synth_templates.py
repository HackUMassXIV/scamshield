"""Offline synthetic call generator: no API key needed, so training works tonight.

Each call is a sequence of CALLER/STUDENT turns built from per-label stages
(opening -> claim -> pressure -> demand -> secrecy -> close), each with several
phrasings and filled slots, interleaved with student replies. ~30% of calls are
about international students.

The benign class deliberately contains HARD NEGATIVES: urgent-but-legit calls
(real bank fraud alert that asks for nothing, pharmacy, landlord leak, the
international student office reminding you about SEVIS paperwork, a store
saying your gift card order shipped). Without them the model learns
"IRS/visa/gift card = scam" and will flag real calls.

Template data is easy to fit, so its test scores are optimistic. The honest
checks are: Gemini-generated calls (ml/gen_gemini.py), Whisper transcripts
(ml/tts_asr_aug.py), and the hand-written scripts in data/calls/scripts.

  python -m ml.synth_templates --per-label 80 --out data/ml/calls_template.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from shared.labels import LABELS

R = random.Random()

SLOTS = {
    "amount": ["600", "850", "1,200", "2,400", "450", "3,000", "975", "1,500"],
    "agency_irs": ["the IRS", "the Internal Revenue Service", "the Treasury Department", "the Social Security Administration", "the Department of Revenue"],
    "agency_imm": ["USCIS", "the immigration department", "Homeland Security", "the Department of Immigration", "the SEVIS compliance office", "ICE"],
    "officer": ["Officer Brooks", "Agent Miller", "Officer Daniel Grant", "Inspector Lee", "Agent Robert Hayes", "Officer Kumar"],
    "card": ["Google Play gift cards", "Apple gift cards", "Target gift cards", "Steam cards", "Visa gift cards", "eBay gift cards"],
    "crypto": ["bitcoin", "a bitcoin ATM", "crypto", "USDT"],
    "wire": ["a wire transfer", "Zelle", "Western Union", "MoneyGram", "Venmo"],
    "company": ["Microsoft", "Apple support", "Windows security", "your antivirus provider", "Amazon tech support"],
    "bank": ["Chase", "Bank of America", "Wells Fargo", "your bank", "the bank fraud department", "Citi"],
    "relative": ["your cousin", "your brother", "your son", "your nephew", "your daughter", "your sister"],
    "place": ["Mexico", "the county jail", "the hospital", "a police station in Boston", "the airport"],
    "employer": ["a marketing agency", "a remote data entry company", "a research lab", "a logistics startup", "a nonprofit"],
    "item": ["office equipment", "a laptop for training", "software licenses", "supplies"],
    "apt": ["the two bedroom on North Pleasant Street", "the studio near campus", "the sublet on Main Street", "the apartment downtown"],
    "store": ["CVS", "Walgreens", "Target", "Walmart", "the campus pharmacy"],
    "country": ["India", "China", "Nigeria", "Brazil", "Korea", "Vietnam", "Pakistan"],
    "day": ["Monday", "Tuesday", "Thursday", "Friday", "tomorrow"],
}


def fill(s: str) -> str:
    out = s
    for k, vals in SLOTS.items():
        while "{" + k + "}" in out:
            out = out.replace("{" + k + "}", R.choice(vals), 1)
    return out


# Student replies by attitude. Mixed in so the classifier can't rely on one speaker.
STUDENT = {
    "nervous": ["oh no what do i do", "i didn't know that", "okay okay please tell me", "am i in trouble",
                "i'm really scared right now", "what happens if i can't pay", "i don't have that much money",
                "can i fix this today", "please don't arrest me", "how much exactly"],
    "comply": ["okay how many cards do i need", "where do i buy them", "okay i'm going to the store now",
               "should i read you the numbers", "okay i will send it", "how do i send the money",
               "okay i won't tell anyone", "i'm at the store now"],
    "skeptical": ["can you give me your badge number", "why can't you send a letter", "this sounds weird",
                  "i'll call you back on the official number", "how do i know this is real"],
    "normal": ["okay sounds good", "sure thanks", "yeah that works", "got it", "okay thank you",
               "no problem", "alright see you then", "oh nice thanks for letting me know", "okay"],
}

# ------------------------------------------------------------------- scams
SCAM = {
    "irs_gov": {
        "open": ["hello this is {officer} calling from {agency_irs}", "this is an urgent call from {agency_irs} about your tax file",
                 "good afternoon this is the tax enforcement division of {agency_irs}", "this message is from {agency_irs} legal department"],
        "claim": ["our records show you owe {amount} dollars in back taxes", "there is a lawsuit filed against your name for tax fraud",
                  "your social security number has been suspended due to suspicious activity", "you made an error on last year's tax return and owe a penalty",
                  "your tax id was used in a money laundering case"],
        "pressure": ["if you do not pay today a warrant will be issued for your arrest", "local police will come to your address within the hour",
                     "this is your final notice before legal action", "your bank accounts will be frozen today", "you could face jail time"],
        "demand": ["you need to pay {amount} dollars right now using {card}", "you must settle this immediately through {wire}",
                   "go to a store and buy {card} and read me the numbers", "the payment must be made with {crypto} today",
                   "please confirm your full social security number to verify"],
        "secret": ["do not hang up and do not tell anyone about this call", "stay on the line with me while you go to the store",
                   "this is confidential do not discuss it with your family", "do not talk to the store clerk about why you are buying the cards"],
    },
    "immigration_uscis": {
        "open": ["hello this is {officer} from {agency_imm}", "this is a call from {agency_imm} regarding your student visa",
                 "this is the immigration enforcement unit calling about your status", "we are calling from {agency_imm} about your I-20"],
        "claim": ["there is a problem with your visa and your SEVIS record has been flagged", "your F-1 status has been terminated due to missing documents",
                  "your passport was found in a package with illegal items", "you did not report your address change and you are now out of status",
                  "your OPT application was rejected because of a fee error"],
        "pressure": ["you will be deported back to {country} if this is not fixed today", "officers will detain you at your apartment tonight",
                     "your visa will be cancelled within two hours", "you will never be allowed to enter the united states again",
                     "a warrant for your deportation has been issued"],
        "demand": ["you must pay a processing fee of {amount} dollars right now", "the fine can only be paid with {card}",
                   "send the bond amount through {wire} immediately", "pay the reinstatement fee in {crypto} to clear your record",
                   "read me your passport number and bank details to verify"],
        "secret": ["do not contact your university or anyone else", "do not tell your international office this is a federal matter",
                   "stay on the phone with me until this is resolved", "if you hang up the case will be escalated"],
    },
    "tech_support": {
        "open": ["hello this is {company} technical department", "this is {company} calling about your computer",
                 "we are calling from {company} security center", "hi this is the support team from {company}"],
        "claim": ["we detected a virus on your computer that is stealing your bank information", "your computer has been sending error reports to our servers",
                  "hackers have accessed your laptop and your accounts", "your license has expired and your device is compromised"],
        "pressure": ["if we do not fix it now you will lose all your files", "your bank account is at risk right now",
                     "the hackers are using your computer as we speak", "this needs to be fixed in the next few minutes"],
        "demand": ["please download anydesk so i can access your computer", "you need to pay {amount} dollars for the security protection",
                   "log into your bank account so i can check for fraud", "buy {card} to pay for the repair service",
                   "type this code into your computer and give me remote access"],
        "secret": ["do not turn off your computer", "do not call your bank they may be involved",
                   "stay on the line while i fix it", "do not tell anyone or the hackers will know"],
    },
    "rental_deposit": {
        "open": ["hi i'm calling about {apt} you asked about online", "hello this is the owner of {apt}",
                 "hi you messaged about the room listing near campus", "hello i'm the landlord for the place you saw on facebook"],
        "claim": ["i'm out of the country right now so i can't show it in person", "a lot of students want this place so it will go fast",
                  "the rent is only 600 a month utilities included", "i'm a missionary working abroad so my agent handles the keys"],
        "pressure": ["if you want it you need to secure it today", "someone else is ready to pay right now",
                     "i can only hold it for one hour", "you need to decide now or i'll give it to the next person"],
        "demand": ["send a {amount} dollar deposit through {wire} and i'll mail you the keys", "pay the first month and deposit with {card}",
                   "you can send the deposit with {crypto}", "send the security deposit before you see the apartment"],
        "secret": ["you can't see it before paying because i'm away", "don't worry about a lease we can sign later",
                   "please don't contact the property management company", "just trust me i'll send the keys by mail"],
    },
    "fake_job_check": {
        "open": ["hi this is the hiring manager from {employer}", "hello congratulations you got the remote assistant job with {employer}",
                 "hi i'm calling about the part time job you applied for", "hello this is HR from {employer} about your interview"],
        "claim": ["we mailed you a check for {amount} dollars to buy {item}", "you were selected without an interview",
                  "your first task is to process a payment for us", "we sent you more money than needed by mistake"],
        "pressure": ["you need to deposit the check today to start", "the job offer expires if you don't act now",
                     "the vendor needs the money this afternoon", "we need this done before your first day"],
        "demand": ["deposit the check and send the difference back through {wire}", "buy {card} with the extra money and send me photos",
                   "send {amount} dollars to our equipment vendor using {crypto}", "give me your bank login so we can set up direct deposit"],
        "secret": ["don't tell your bank what the money is for", "keep this between us until you are onboarded",
                   "if the bank asks just say it's a gift", "don't wait for the check to clear just send it now"],
    },
    "family_emergency": {
        "open": ["grandma it's me please don't be mad", "hi it's me i'm in trouble please help", "hello this is {officer} i'm calling about {relative}",
                 "mom it's me i lost my phone this is a friend's number"],
        "claim": ["i got into a car accident and i'm at {place}", "i've been arrested and i need bail money", "{relative} was in an accident and is in {place}",
                  "i'm stuck in {place} and they took my wallet"],
        "pressure": ["i need the money right now or i'll stay locked up", "please hurry the lawyer is waiting", "they said i only have an hour",
                     "please i'm so scared just help me"],
        "demand": ["can you send {amount} dollars through {wire}", "the lawyer said to pay the bail with {card}", "please send the money in {crypto}",
                   "a courier will come to your door to pick up cash"],
        "secret": ["please don't tell mom and dad", "don't call anyone else just send it", "keep this secret i'm so embarrassed",
                   "don't call my old number it's broken"],
    },
    "bank_impersonation": {
        "open": ["hello this is the fraud department from {bank}", "this is {bank} security calling about your debit card",
                 "hi this is an automated alert from {bank} press one to speak with an agent", "this is {bank} we noticed suspicious activity"],
        "claim": ["someone tried to spend {amount} dollars on your account", "your account has been compromised and will be locked",
                  "we detected a login from another country", "a large transfer is pending from your account"],
        "pressure": ["we need to secure your account in the next ten minutes", "if you don't act now the money will be gone",
                     "your account will be frozen today", "the fraudster is trying again right now"],
        "demand": ["please read me the one time code we just texted you", "move your money to this safe account using {wire}",
                   "confirm your full card number and pin to verify", "send yourself money through {wire} to reverse the charge",
                   "give me your online banking password so i can block the transfer"],
        "secret": ["do not go to your branch they may be involved", "do not hang up or the transfer will go through",
                   "don't tell anyone this is an internal investigation", "stay on the line with me"],
    },
}

# ------------------------------------------------------------------- benign
BENIGN_URGENT = {
    "real_bank_alert": [
        "hi this is an automated fraud alert from {bank}", "we noticed a purchase of {amount} dollars at an electronics store",
        "if you made this purchase no action is needed", "if you did not please call the number on the back of your card",
        "we will never ask for your pin or one time code over the phone", "you can also check this in your banking app"],
    "pharmacy": [
        "hi this is {store} pharmacy calling", "your prescription is ready for pickup", "your insurance needs a quick update before we can fill it",
        "please bring your insurance card when you come in", "we close at nine tonight", "you can pay at the counter as usual"],
    "landlord_leak": [
        "hi this is your landlord", "the unit above you has a leak and water is coming through the ceiling",
        "i need to send a plumber in today around three", "can you move your laptop and electronics away from the window",
        "no need to pay anything it's covered by the building", "just text me if the time doesn't work"],
    "isss_reminder": [
        "hi this is the international student office at the university", "this is a reminder that your SEVIS record needs your updated address",
        "please log into the student portal and update it before {day}", "there is no fee for this update",
        "if you have questions you can stop by our office during walk in hours", "we will never ask for payment over the phone"],
    "irs_appointment": [
        "hi this is the volunteer tax assistance clinic on campus", "we have you scheduled to file your tax return on {day}",
        "please bring your W-2 and your 1098-T form", "for international students please also bring your passport and visa documents",
        "the service is free", "call the front desk if you need to reschedule"],
    "gift_card_order": [
        "hi this is customer service from {store}", "the gift card you ordered for your friend's birthday has shipped",
        "it should arrive by {day}", "you can track it with the link in your email",
        "you don't need to do anything else", "thanks for shopping with us"],
    "doctor_results": [
        "hi this is the university health center", "your lab results are back and the doctor wants to talk about them",
        "it's nothing urgent but please call back to schedule", "we have openings on {day}", "bring your student insurance card", "have a good day"],
    "car_accident_friend": [
        "hey it's me i'm okay but i got a flat tire on route nine", "can you pick me up i'm at the gas station",
        "my phone is almost dead", "no i don't need money just a ride", "i'll call the tow company myself", "thanks you're the best"],
}
BENIGN_NORMAL = {
    "friend_homework": ["hey are you working on the problem set tonight", "i'm stuck on question three", "want to meet at the library at seven",
                        "bring your notes from lecture", "i'll grab coffee on the way", "cool see you there"],
    "parent_checkin": ["hi beta how are you doing", "did you eat dinner", "how are your classes going", "your cousin says hi",
                       "call us this weekend", "okay take care"],
    "professor": ["hi this is professor reed returning your call", "yes you can come to office hours on {day}", "bring your draft of the project",
                  "we can talk about the research position", "the deadline is next week", "see you then"],
    "delivery": ["hi this is the delivery driver", "i'm outside your building with your package", "is the front door code still the same",
                 "i'll leave it with the front desk", "have a nice day", "bye"],
    "dentist": ["hi this is the dental office", "this is a reminder of your cleaning on {day}", "please arrive ten minutes early",
                "you can cancel up to a day before", "see you soon", "bye"],
    "roommate": ["hey the internet is down again", "i already called the provider", "they're coming on {day}",
                 "also can you buy milk on the way home", "i'll pay you back on venmo later", "thanks"],
    "job_real": ["hi this is the campus dining office", "we got your application for the student job", "can you come in for an interview on {day}",
                 "bring your student id and a copy of your schedule", "there's no fee or anything to pay", "see you then"],
}


def _turns_from_lines(lines: list[str], attitude: str) -> list[dict]:
    turns = []
    for line in lines:
        turns.append({"speaker": "CALLER", "text": fill(line)})
        if R.random() < 0.6:
            # 20% polite/neutral replies in scam calls too, so "okay thanks" isn't a benign-only cue
            mood = "normal" if (attitude != "normal" and R.random() < 0.2) else attitude
            turns.append({"speaker": "STUDENT", "text": R.choice(STUDENT[mood])})
    return turns


def make_scam(label: str, intl: bool) -> list[dict]:
    st = SCAM[label]
    order = ["open", "claim", "pressure", "demand", "secret"]
    if R.random() < 0.3:  # some calls drop a stage (harder, shorter)
        order.remove(R.choice(["pressure", "secret"]))
    lines = [R.choice(st[s]) for s in order]
    if R.random() < 0.5:  # a second demand or pressure line
        lines.insert(len(lines) - 1, R.choice(st[R.choice(["demand", "pressure"])]))
    if intl and label != "immigration_uscis":
        lines.insert(1, R.choice(["since you are on a student visa this could affect your status",
                                  "as an international student you could be deported for this",
                                  "this also affects your F-1 visa"]))
    attitude = R.choice(["nervous", "nervous", "comply", "skeptical"])
    turns = _turns_from_lines(lines, attitude)
    if attitude == "comply" or R.random() < 0.4:
        turns.append({"speaker": "STUDENT", "text": R.choice(STUDENT["comply"])})
    return turns


def make_benign(urgent: bool, intl: bool) -> tuple[list[dict], str]:
    pool = BENIGN_URGENT if urgent else BENIGN_NORMAL
    sub = R.choice(list(pool))
    lines = list(pool[sub])
    if R.random() < 0.3:
        lines.pop(R.randrange(1, len(lines)))
    if intl and R.random() < 0.6:
        lines.insert(1, R.choice(["also are you going home to {country} for winter break",
                                  "by the way the international student office has a dinner on {day}",
                                  "how is your family back in {country}"]))
    return _turns_from_lines(lines, "normal"), sub


def generate(per_label: int, seed: int = 7, benign_mult: float = 2.0) -> list[dict]:
    R.seed(seed)
    calls = []
    for label in LABELS:
        n = int(per_label * (benign_mult if label == "benign" else 1))
        for i in range(n):
            intl = R.random() < 0.3
            if label == "benign":
                urgent = i % 2 == 0  # half of benign calls are urgent-but-legit
                turns, sub = make_benign(urgent, intl)
                subtype = ("urgent:" if urgent else "normal:") + sub
            else:
                turns, subtype = make_scam(label, intl), "scam"
            calls.append({"call_id": f"tpl-{label}-{i:03d}", "label": label, "subtype": subtype,
                          "intl": intl, "source": "template", "turns": turns})
    return calls


def write_jsonl(calls: list[dict], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-label", type=int, default=80)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/ml/calls_template.jsonl")
    a = ap.parse_args()
    calls = generate(a.per_label, a.seed)
    write_jsonl(calls, a.out)
    print(f"wrote {len(calls)} calls to {a.out}")


if __name__ == "__main__":
    main()

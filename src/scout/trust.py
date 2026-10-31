"""Trust scoring (architecture plan §6.2, docs/phase1-plan.md §3 step 9, task 1.9).

Plain code, no LLM. Each posting gets a score and the reasons for it, e.g.
["+2 ats_board:ashby", "+1 confirmed_on_board", "-5 asks_for_fee"]. Suspicious postings
(score below 0) are flagged and kept, never deleted: see v_suspicious.

Good signs
  +2 ats_board:<ats>      linked to a known job-board system (Greenhouse, Lever, Ashby,
                          Workday, SmartRecruiters, …)
  +2 job_bank             a Canada Job Bank posting (employers are verified by the federal
                          government)
  +2 employer_domain      linked to a site whose domain carries the employer's name
  +1 confirmed_on_board   the posting was found on the employer's job board itself

Red flags (checked in title, description, salary text and link)
  -5 asks_for_fee         registration, training or starter-kit fees; pay up front
  -4 asks_for_sin_or_bank SIN, bank account details, void cheque, ID documents
  -5 money_mule_duties    cashing cheques, reshipping parcels, crypto or gift-card transfers
  -4 chat_app_contact     contact through Telegram, WhatsApp, Signal or WeChat
  -2 free_email_contact   a Gmail/Yahoo/Hotmail/Outlook address to apply or contact
  -2 pay_far_above_normal over $500,000 a year (or $250 an hour) for these roles
  -2 no_employer_name     the posting names no employer
  -1 board_name_unrelated a job-board name that doesn't resemble the employer (a board
                          posting for someone else, e.g. an aggregator)

Typical genuine postings score +2 to +5; one serious red flag takes a posting below 0.
Lookalike domains and a domain-reputation check are left for later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KNOWN_ATS = {
    "ashby", "greenhouse", "lever", "workday", "smartrecruiters", "workable", "oracle",
    "rippling", "comeet", "kula", "gohire",
}  # fmt: skip

_FEE = re.compile(
    r"(?i)\b(registration|training|application|processing|onboarding|starter[- ]kit|equipment)"
    r"\s+fees?\b|\bpay\s+(a|the|an)\s+(small\s+)?fee\b|\bupfront\s+(payment|fee)\b"
    r"|\bfee\s+is\s+refundable\b"
)
_SIN_BANK = re.compile(
    r"\bSIN\b|(?i:\bsocial\s+insurance\s+number\b|\bbank\s+(account\s+)?(details|information|number)\b"
    r"|\bvoid\s+cheque\b|\bcopy\s+of\s+(your\s+)?(passport|driver'?s\s+licen[cs]e|photo\s+id)\b)"
)
_MULE = re.compile(
    r"(?i)\b(cash(ing)?\s+(a\s+|the\s+)?cheques?|deposit\s+(a\s+|the\s+)?cheques?"
    r"|re-?ship(ping)?\s+(packages|parcels|items)|package\s+forwarding|parcel\s+forwarding"
    r"|(bitcoin|crypto(currency)?)\s+(transfers?|payments?|atm)|buy\s+gift\s+cards?"
    r"|money\s+transfer\s+agent|payment\s+processing\s+agent)\b"
)
_APPS = r"(telegram|whats\s?app|signal\s+app|wechat)"
# Contact wording near the app name, so "experience building Telegram bots" doesn't count.
_CHAT = re.compile(
    rf"(?i)\b(message|contact|text|reach|apply|chat|dm|add)\b[^.\n]{{0,40}}\b{_APPS}\b"
    rf"|\b{_APPS}\b[^.\n]{{0,25}}(\bat\b|:|\bnumber\b|\bid\b|\bhandle\b|\+?\d{{6,}})"
    r"|[?&]contact=(telegram|whatsapp)"
)
_FREE_EMAIL = re.compile(
    r"(?i)\b[\w.+-]+@(gmail|googlemail|yahoo|hotmail|outlook|live|aol|icloud|proton(mail)?|gmx)"
    r"\.(com|ca|me|net)\b"
)
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*([kK])?")

_STOP = {"the", "and", "of", "inc", "group"}
# Boards named after the company (Oracle and Workday use tenant codes instead).
_BOARD_IS_COMPANY = KNOWN_ATS - {"oracle", "workday"}

MAX_ANNUAL = 500_000
MAX_HOURLY = 250
_PER_YEAR = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}


@dataclass(frozen=True)
class TrustInput:
    title: str
    employer_name: str | None
    employer_norm: str | None
    ats: str | None
    ats_board: str | None
    jump_url: str | None
    link_host: str | None
    is_job_bank: bool
    found_via: str
    enriched: bool
    description: str | None = None
    salary_text: str | None = None
    pay_max: float | None = None
    pay_period: str | None = None


def _salary_period(text: str) -> str | None:
    t = text.casefold()
    for word, period in (("hour", "hour"), ("annual", "year"), ("year", "year"),
                         ("month", "month"), ("week", "week"), ("day", "day")):  # fmt: skip
        if word in t:
            return period
    return None


def annual_pay(salary_text: str | None, pay_max: float | None, pay_period: str | None):
    """Highest yearly-equivalent pay stated, or None."""
    best = None
    if pay_max and pay_period in _PER_YEAR:
        best = pay_max * _PER_YEAR[pay_period]
    if salary_text:
        period = _salary_period(salary_text) or "year"
        for amount, k in _MONEY.findall(salary_text):
            value = float(amount.replace(",", "")) * (1000 if k else 1)
            yearly = value * _PER_YEAR[period]
            best = yearly if best is None else max(best, yearly)
    return best


def _host_matches_employer(host: str | None, employer_norm: str | None) -> bool:
    if not host or not employer_norm:
        return False
    labels = [p for p in host.lower().split(".") if p not in ("www", "careers", "jobs", "apply")]
    domain = "".join(labels[:-1]) if len(labels) > 1 else "".join(labels)
    words = [w for w in employer_norm.split() if len(w) > 2 and w not in _STOP]
    if not words:
        return False
    initials = "".join(w[0] for w in employer_norm.split() if w not in _STOP)
    return words[0] in domain or domain == initials or (len(initials) >= 3 and initials in domain)


def _board_matches_employer(board: str | None, employer_norm: str | None) -> bool:
    if not board or not employer_norm:
        return True  # nothing to compare
    b = re.sub(r"[^a-z0-9]", "", board.lower())
    words = employer_norm.split()
    initials = "".join(w[0] for w in words if w not in _STOP)
    joined = "".join(words)
    return (
        any(len(w) > 2 and w in b for w in words)
        or (len(initials) >= 2 and b.startswith(initials))
        or (len(b) >= 3 and b in joined)
    )


def score(p: TrustInput) -> tuple[int, list[str]]:
    reasons: list[tuple[int, str]] = []

    if p.is_job_bank:
        reasons.append((2, "job_bank"))
    if p.ats in KNOWN_ATS:
        reasons.append((2, f"ats_board:{p.ats}"))
    elif _host_matches_employer(p.link_host, p.employer_norm):
        reasons.append((2, "employer_domain"))
    if p.found_via == "ats" or p.enriched:
        reasons.append((1, "confirmed_on_board"))

    text = "\n".join(x for x in (p.title, p.description, p.salary_text, p.jump_url) if x)
    if _FEE.search(text):
        reasons.append((-5, "asks_for_fee"))
    if _SIN_BANK.search(text):
        reasons.append((-4, "asks_for_sin_or_bank"))
    if _MULE.search(text):
        reasons.append((-5, "money_mule_duties"))
    if _CHAT.search(text):
        reasons.append((-4, "chat_app_contact"))
    if _FREE_EMAIL.search(text):
        reasons.append((-2, "free_email_contact"))
    yearly = annual_pay(p.salary_text, p.pay_max, p.pay_period)
    if yearly is not None and yearly > MAX_ANNUAL:
        reasons.append((-2, "pay_far_above_normal"))
    if not (p.employer_name or "").strip():
        reasons.append((-2, "no_employer_name"))
    if p.ats in _BOARD_IS_COMPANY and not _board_matches_employer(p.ats_board, p.employer_norm):
        reasons.append((-1, "board_name_unrelated"))

    return sum(points for points, _ in reasons), [f"{n:+d} {why}" for n, why in reasons]


def rescore(conn) -> tuple[int, int]:
    """Score every posting; write only changes. Returns (suspicious, changed)."""
    from urllib.parse import urlparse

    rows = conn.execute(
        """
        SELECT p.id, p.title, p.employer_name, e.name_norm, p.ats, p.ats_board, p.jump_url,
               p.is_job_bank, p.found_via, p.enriched_at IS NOT NULL, p.description,
               p.salary_text, p.pay_max, p.pay_period, p.trust_score, p.trust_reasons
        FROM postings p LEFT JOIN employers e ON e.id = p.employer_id
        """
    ).fetchall()
    changes, suspicious = [], 0
    for r in rows:
        inp = TrustInput(
            title=r[1], employer_name=r[2], employer_norm=r[3], ats=r[4], ats_board=r[5],
            jump_url=r[6], link_host=urlparse(r[6]).netloc if r[6] else None,
            is_job_bank=r[7], found_via=r[8], enriched=r[9], description=r[10],
            salary_text=r[11], pay_max=float(r[12]) if r[12] is not None else None,
            pay_period=r[13],
        )  # fmt: skip
        value, reasons = score(inp)
        suspicious += value < 0
        if value != r[14] or reasons != list(r[15] or []):
            changes.append((value, reasons, r[0]))
    if changes:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE postings SET trust_score = %s, trust_reasons = %s WHERE id = %s", changes
            )
    return suspicious, len(changes)

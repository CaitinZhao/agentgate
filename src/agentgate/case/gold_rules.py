"""Shared "is this dataset answer an unambiguous quantity?" rule set.

Both the FinanceBench adapter and `agentgate autoadapt` derive a numeric gold from a
dataset's answer text. Doing that by guesswork is how CORRECT answers get marked wrong
(a year read as a value, a day inside a date, one item of an enumeration, a yes/no
conclusion reciting percentages along the way). The rules below are deliberately
conservative — when the answer is not an unambiguous quantity the caller must fall back
to free_text + reference answer (AGENTS.md: judge_required over a forced verdict).

Return value: (value, reason_if_rejected). `reason_if_rejected` is None when accepted.
"""
import re
from typing import Optional, Tuple

NUM_RE = re.compile(r"-?\$?\s*\d[\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# "which / who / where" ask for an ENTITY; "when" asks for a point in time
ENTITY_Q_RE = re.compile(r"^\s*(which|who|whom|whose|where|when|name\s+the|list\s+the)\b", re.I)
# yes/no questions ("Does Adobe have...") stay boolean even with numbers in the prose
YESNO_Q_RE = re.compile(r"^\s*(does|do|did|is|are|was|were|has|have|had|can|could|will|should)\b", re.I)
FY_RE = re.compile(r"\b(?:fy\s*)?((?:19|20)\d{2})\b", re.I)
# a figure is a quantity only when a unit / percent sign follows it
UNIT_AFTER_RE = re.compile(r"^\s*(%|percent|percentage|million|billion|thousand|亿|万|元)", re.I)
DATE_CTX_RE = re.compile(
    r"(january|february|march|april|june|july|august|september|october|november|december"
    r"|as of|ended|ending|fiscal year end|年\s*\d|\d{4}年)", re.I)
ORDINAL_AFTER_RE = re.compile(r"^\s*(st|nd|rd|th)\b", re.I)
# a bulleted/numbered list is an enumeration, not a single value. A leading minus on a
# bare figure ("-14.76%") must NOT be read as a bullet.
ENUM_RE = re.compile(r"^\s*(?:[-*•]\s+|\d{1,2}[.)]\s|[•*]\s*)\S", re.M)


def _date_adjacent(a: str, m) -> bool:
    pre = a[max(0, m.start() - 28):m.start()]
    post = a[m.end():m.end() + 6]
    return bool(DATE_CTX_RE.search(pre)) or bool(ORDINAL_AFTER_RE.match(post))


def quantity_value(answer: str, question: str) -> Tuple[Optional[float], Optional[str]]:
    """The unambiguous quantity in `answer`s prose, or (None, reason) when there is none.

    Rejections (each maps to a real mis-judged case in the FinanceBench bank):
    - entity / period question                -> "entity question"
    - yes/no question with a stance answer    -> "yes/no question"
    - bulleted/numbered enumeration           -> "enumeration"
    - no figure is unit-bearing               -> "no unit-bearing value"
    - several unit-bearing figures, no period to disambiguate -> "ambiguous"
    """
    a = str(answer or "").strip().lstrip("$").replace(",", "")
    q = str(question or "")
    if ENTITY_Q_RE.match(q):
        return None, "entity question"
    ans_low = a.strip().lower()
    if YESNO_Q_RE.match(q) and (ans_low.startswith(("yes", "no")) or YESNO_Q_RE.match(ans_low)):
        return None, "yes/no question"
    if ENUM_RE.search(a):
        return None, "enumeration"
    # a bare number carrying the whole answer ("77.78") is unambiguous by construction
    bare = NUM_RE.fullmatch(a.strip())
    if bare:
        try:
            return float(bare.group(0).replace(",", "").replace("$", "").strip()), None
        except ValueError:
            pass
    fm = FY_RE.search(q.lower())
    want_year = fm.group(1) if fm else None
    units, aligned = [], []
    for m in NUM_RE.finditer(a):
        raw = m.group(0).replace(",", "").replace("$", "").strip()
        try:
            v = float(raw)
        except ValueError:
            continue
        if YEAR_RE.match(raw.split(".")[0]):
            continue                        # a bare year is a period, not a quantity
        if _date_adjacent(a, m):
            continue                        # part of a date (a day, a year in prose)
        if not UNIT_AFTER_RE.match(a[m.end():m.end() + 16]) and m.start() > 2:
            continue                        # mid-prose figure with no unit
        units.append(v)
        if want_year:
            # the period belongs to the CLOSEST figure: one window may span two figures
            # ("in FY2022 was 0.62%, compared to -14.76% in FY2021")
            best = None
            for ym in FY_RE.finditer(a):
                if ym.group(1) == want_year:
                    d = abs(ym.start() - m.start())
                    best = d if best is None else min(best, d)
            if best is not None and best <= 40:
                aligned.append((best, v))
    if aligned:
        aligned.sort(key=lambda t: t[0])
        return aligned[0][1], None
    if not units:
        return None, "no unit-bearing value"
    if len({round(v, 6) for v in units}) > 1:
        return None, "ambiguous (several values, no period to disambiguate)"
    return units[-1], None

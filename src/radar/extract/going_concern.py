"""Wording rules of the going concern, shared by the deterministic extractor and the LLM
validator (Phase 3.4c). A doubt counts only when it is stated for now: neither negated
("not impacted", "no substantial doubt"), nor hypothetical ("could raise substantial
doubt", "if we cannot raise capital"), nor the description of the evaluation management is
required to perform, nor alleviated by management's plans. The liquidity lesson applies: a
hypothetical risk is not a situation observed."""

from __future__ import annotations

import re

TOPIC_RE = re.compile(r"(?i)\bgoing[\s-]concern\b")
# the doubt wording of the accounting standards (ASC 205-40, IAS 1): substantial doubt or a
# material uncertainty about the ability to continue as a going concern
DOUBT_RE = re.compile(
    r"(?i)\b(?:substantial|significant|material)\s+(?:doubt|uncertaint(?:y|ies))\b"
    r"|\b(?:unable|not\s+be\s+able)\s+to\s+continue\s+as\s+a\s+going[\s-]concern\b"
)
DENIAL_RE = re.compile(
    r"(?i)\bno\s+(?:substantial|significant|material)\s+(?:doubt|uncertaint(?:y|ies))\b"
    r"|\bnot\s+(?:impacted|affected|in\s+doubt|threatened|at\s+risk)\b|\bdoes\s+not\s+cast\b"
    r"|\b(?:do|does|did)\s+not\s+(?:raise|give\s+rise\s+to|indicate)\b"
    r"|\bno\s+longer\s+(?:raises?|exists?|have|has)\b"
)
NEGATION_RE = re.compile(r"(?i)\b(?:no|not|without|never|neither|nor|free\s+of|absence\s+of)\b")
ALLEVIATE_RE = re.compile(r"(?i)\b(?:alleviat|mitigat)(?:e|es|ed|ing)\b")
# before an alleviation verb: plans that may not, are intended to or would alleviate the
# doubt leave the doubt in place
UNCERTAIN_ALLEVIATION_RE = re.compile(
    r"(?i)\b(?:not|cannot|may|might|could|would|intended|designed|expected|sufficient|"
    r"probable|whether|if|unless|should)\b"
)
# before a doubt marker: a hypothetical ("could raise substantial doubt") or the description
# of the evaluation ("evaluate whether ... raise substantial doubt"), unless a conclusion
# follows the evaluation word ("evaluated ... and concluded that substantial doubt exists")
HYPOTHETICAL_RE = re.compile(
    r"(?i)\b(?:may|might|could|would|can|if|should|unless|potential|future|any|whether)\b"
)
EVALUATION_RE = re.compile(
    r"(?i)\b(?:whether|evaluat(?:e|es|ed|ing|ion)|assess(?:es|ed|ing|ment)?|required?\s+to)\b"
)
CONCLUSION_RE = re.compile(r"(?i)\b(?:concluded|determined|identified|believes?|exists?|existed)\b")
CONDITIONAL_START_RE = re.compile(r"(?i)\s*(?:if|should|unless|were)\b")
CONDITION_RE = re.compile(r"(?i)\b(?:if|unless)\b")


def words(text: str) -> list[str]:
    return [w.strip(",;:()\"'“”‘’") for w in text.split()]


def negated(sentence: str, marker_start: int) -> bool:
    """A negation word within the five words before the marker in the same sentence."""
    before = sentence[:marker_start].split()
    return any(NEGATION_RE.fullmatch(w.strip(",;:()")) for w in before[-5:])


def reading(sentence: str, doubt_in_quote: bool | None = None) -> dict[str, bool]:
    """What one sentence says about the going concern: a doubt stated for now, a doubt only
    hypothetical or described, a denial, an effective alleviation by management's plans."""
    if doubt_in_quote is None:
        doubt_in_quote = bool(DOUBT_RE.search(sentence))
    stated = hypothetical = False
    denied = bool(DENIAL_RE.search(sentence))
    for m in DOUBT_RE.finditer(sentence):
        before = sentence[: m.start()]
        window = words(before)[-6:]
        alleviation_near = any(ALLEVIATE_RE.fullmatch(w) for w in window)
        if negated(sentence, m.start()) and not alleviation_near:
            denied = True  # "no substantial doubt", "not ... raise substantial doubt"
            continue
        # "management has concluded that the company may be unable to continue as a going
        # concern" is a conclusion of doubt: a modal after a conclusion word is the standard's
        # own wording, not a hypothetical; "if", "unless" anywhere before the marker is one
        conditional = bool(CONDITIONAL_START_RE.match(before)) or bool(CONDITION_RE.search(before))
        concluded = bool(CONCLUSION_RE.search(before))
        modal = (
            any(HYPOTHETICAL_RE.fullmatch(w) for w in window)
            and not alleviation_near
            and not concluded
        )
        last_evaluation = None
        for em in EVALUATION_RE.finditer(before):
            last_evaluation = em.end()
        described = last_evaluation is not None and not CONCLUSION_RE.search(
            before[last_evaluation:]
        )
        if modal or described or conditional:
            hypothetical = True
            continue
        stated = True
    alleviated = False
    if doubt_in_quote:
        for m in ALLEVIATE_RE.finditer(sentence):
            window = words(sentence[: m.start()])[-4:]
            if not any(UNCERTAIN_ALLEVIATION_RE.fullmatch(w) for w in window):
                alleviated = True
    return {
        "stated": stated, "hypothetical": hypothetical, "denied": denied, "alleviated": alleviated
    }  # fmt: skip


def doubt_stated_now(sentence: str) -> tuple[bool, str | None]:
    """True when the sentence states a current doubt; otherwise why not (alleviated,
    negated, hypothetical, none), the reason the deterministic extractor records."""
    r = reading(sentence)
    if r["alleviated"]:
        return False, "alleviated"
    if r["stated"]:
        return True, None
    if r["denied"]:
        return False, "negated"
    if r["hypothetical"]:
        return False, "hypothetical"
    return False, "none"

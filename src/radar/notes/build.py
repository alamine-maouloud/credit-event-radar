"""Build the committee note from an Alert: deterministic sections always, model prose only
when given, and only its verified sentences."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from radar.alerts.model import Alert
from radar.audit import stable_hash
from radar.notes.model import CommitteeNote, NoteClaim, NoteIndicator, NoteSection, verify_claim

DISCLAIMER = {
    "fr": (
        "Brouillon généré automatiquement à partir de sources publiques, à valider par "
        "l'analyste. Aucune recommandation d'investissement."
    ),
    "en": (
        "Draft generated automatically from public sources, to be validated by the analyst. "
        "No investment recommendation."
    ),
}
NOT_AVAILABLE = {"fr": "Non disponible dans les sources", "en": "Not available in the sources"}
CHECKS: dict[str, dict[str, list[str]]] = {
    "rating": {
        "fr": [
            "Vérifier la motivation de l'agence et tout placement sous surveillance dans le "
            "communiqué officiel",
            "Vérifier la position des autres agences par rapport à la frontière IG/HY et "
            "leurs prochaines revues",
        ],
        "en": [
            "Confirm the agency's rationale and any watch placement in the official release",
            "Check the other agencies' positions against the IG/HY boundary and their next reviews",
        ],
    },
    "going_concern": {
        "fr": [
            "Vérifier les plans de la direction et la formulation du doute dans le rapport "
            "complet et le rapport d'audit"
        ],
        "en": [
            "Confirm management's plans and the going concern wording in the full filing "
            "and the auditor's report"
        ],
    },
    "covenant": {
        "fr": [
            "Vérifier les termes des covenants, les conditions du waiver et la prochaine "
            "date de test dans le contrat de crédit"
        ],
        "en": [
            "Confirm the covenant terms, the waiver conditions and the next test date in "
            "the credit agreement"
        ],
    },
    "liquidity": {
        "fr": ["Vérifier la trésorerie, les lignes non tirées et les prochaines échéances"],
        "en": ["Confirm cash, undrawn facilities and the next maturities"],
    },
    "guidance": {
        "fr": ["Vérifier le changement de guidance contre la guidance précédente et le consensus"],
        "en": ["Confirm the guidance change against the prior guidance and consensus"],
    },
    "issuance": {
        "fr": [
            "Vérifier les termes finaux dans le term sheet (montant, coupon, maturité, séniorité)"
        ],
        "en": ["Confirm the final terms in the term sheet (amount, coupon, maturity, seniority)"],
    },
    "other": {
        "fr": ["Vérifier l'événement dans le document source et son impact sur le crédit"],
        "en": ["Confirm the event in the source document and its credit impact"],
    },
}
RATING_INDICATORS = (
    ("Agency", "agency"),
    ("Rating before", "old_rating"),
    ("Rating after", "new_rating"),
    ("Outlook after", "new_outlook"),
    ("Watch", "watch"),
)
ISSUANCE_INDICATORS = (
    ("Amount", "amount"),
    ("Currency", "currency"),
    ("Coupon", "coupon"),
    ("Maturity", "maturity"),
    ("Seniority", "seniority"),
)


def _cites(indices: list[int]) -> str:
    return "".join(f"[{i}]" for i in indices)


def _deterministic_why(alert: Alert) -> list[NoteClaim]:
    all_facts = [f.index for f in alert.key_facts] or [f.index for f in alert.facts[:1]]
    claims = []
    for rule in alert.triggered_rules:
        text = f"{rule.id}: {rule.description or rule.id}. {rule.reason} {_cites(all_facts)}"
        claims.append(
            NoteClaim(
                text=text.strip(),
                citations=all_facts,
                status="VERIFIED",
                checks={"deterministic": True, "rules_engine": True},
            )
        )
    return claims


def _deterministic_checks(alert: Alert, lang: str) -> list[NoteClaim]:
    keys: list[str] = []
    fields = alert.fields
    if alert.family == "rating":
        keys.append("rating")
    elif alert.family == "earnings":
        keys += [f for f in (fields.get("flags") or []) if f in CHECKS]
        if fields.get("guidance_status"):
            keys.append("guidance")
    elif alert.family == "issuance":
        keys.append("issuance")
    if not keys:
        keys.append("other")
    claims = []
    for key in keys:
        related = [f.index for f in alert.facts if (f.field or "").endswith(key)] or [
            f.index for f in alert.key_facts
        ]
        for text in CHECKS[key][lang]:
            claims.append(
                NoteClaim(
                    text=f"{text} {_cites(related)}".strip(),
                    citations=related,
                    status="VERIFIED",
                    checks={"deterministic": True},
                )
            )
    return claims


def _indicators(alert: Alert, lang: str) -> list[NoteIndicator]:
    f = alert.fields
    out: list[NoteIndicator] = []
    if lang == "en":
        src = "event fields (verified spans)"
    else:
        src = "champs de l'événement (passages vérifiés)"
    if alert.family == "rating":
        for label, key in RATING_INDICATORS:
            if f.get(key):
                out.append(NoteIndicator(label=label, value=str(f[key]), source=src))
    elif alert.family == "earnings":
        if f.get("period"):
            out.append(NoteIndicator(label="Period", value=str(f["period"]), source=src))
        if f.get("guidance_status"):
            value = str(f["guidance_status"])
            if f.get("guidance_old") is not None and f.get("guidance_new") is not None:
                value += f" ({f['guidance_old']} → {f['guidance_new']})"
            out.append(NoteIndicator(label="Guidance", value=value, source=src))
        if f.get("flags"):
            out.append(NoteIndicator(label="Flags", value=", ".join(f["flags"]), source=src))
    elif alert.family == "issuance":
        for label, key in ISSUANCE_INDICATORS:
            if f.get(key) is not None:
                out.append(NoteIndicator(label=label, value=str(f[key]), source=src))
    for r in alert.ratings_after:
        when = "after" if lang == "en" else "après"
        value = f"{r.rating} {r.category}" + (f", outlook {r.outlook}" if r.outlook else "")
        out.append(
            NoteIndicator(
                label=f"{r.agency_name} ({when})",
                value=value,
                source=f"{r.origin}, {r.verification}, as of {r.as_of.isoformat()}",
            )
        )
    if not out:
        out.append(NoteIndicator(label="Leverage", value=NOT_AVAILABLE[lang], source="none"))
    return out


def build_note(
    alert: Alert,
    lang: str,
    *,
    prose: Any | None = None,
    provenance: dict[str, Any] | None = None,
    notice: str | None = None,
    generated_at: datetime | None = None,
) -> CommitteeNote:
    context = " ".join(
        [alert.title, alert.effective_date.isoformat() if alert.effective_date else ""]
        + [f"{r.rating} {r.as_of.isoformat()}" for r in alert.ratings_after]
    )
    unsupported: list[NoteClaim] = []
    if prose is not None:
        why_claims: list[NoteClaim] = []
        check_claims: list[NoteClaim] = []
        for sentence in prose.why_it_matters:
            claim = verify_claim(sentence, alert.facts, context)
            (why_claims if claim.status == "VERIFIED" else unsupported).append(claim)
        for sentence in prose.points_to_verify:
            claim = verify_claim(sentence, alert.facts, context)
            (check_claims if claim.status == "VERIFIED" else unsupported).append(claim)
        why = NoteSection(key="why_it_matters", source="llm", claims=why_claims)
        checks = NoteSection(key="points_to_verify", source="llm", claims=check_claims)
    else:
        why = NoteSection(
            key="why_it_matters", source="deterministic", claims=_deterministic_why(alert)
        )
        checks = NoteSection(
            key="points_to_verify",
            source="deterministic",
            claims=_deterministic_checks(alert, lang),
        )
    verified = len(why.claims) + len(checks.claims)
    note_id = stable_hash(
        {"alert": alert.alert_id, "lang": lang, "prose": bool(prose), "verified": verified}
    )
    return CommitteeNote(
        note_id=note_id,
        event_id=alert.event_id,
        alert_id=alert.alert_id,
        lang=lang,  # type: ignore[arg-type]
        generated_at=generated_at or datetime.now(UTC),
        issuer_id=alert.issuer_id,
        issuer_name=alert.issuer_name,
        universe_label=alert.universe_label,
        priority=alert.priority,
        decision_status=alert.decision_status,
        title=alert.title,
        summary=alert.summary,
        effective_date=alert.effective_date.isoformat() if alert.effective_date else None,
        ratings_after=alert.ratings_after,
        key_facts=alert.facts,
        indicators=_indicators(alert, lang),
        triggered_rules=alert.triggered_rules,
        why_it_matters=why,
        points_to_verify=checks,
        sources=alert.sources,
        verification={"verified": verified, "partial": 0, "unsupported": len(unsupported)},
        unsupported=unsupported,
        model_provenance=provenance,
        rules_version=alert.decision_provenance.rules_version,
        notice=notice,
        disclaimer=DISCLAIMER[lang],
    )

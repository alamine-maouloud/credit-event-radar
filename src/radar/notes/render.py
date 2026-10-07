"""Markdown and HTML renders of the committee note, FR and EN labels, no model."""

from __future__ import annotations

import html

from radar.notes.model import CommitteeNote

SECTION_SOURCE = {
    "fr": {
        "deterministic": "section déterministe (moteur de règles)",
        "llm": "prose générée par le modèle, phrases vérifiées seulement",
    },
    "en": {
        "deterministic": "deterministic section (rules engine)",
        "llm": "model prose, verified sentences only",
    },
}
L = {
    "fr": {
        "title": "NOTE DE COMITÉ DE CRÉDIT",
        "issuer": "Émetteur",
        "priority": "Priorité",
        "event": "Événement",
        "date": "Date d'effet",
        "ratings": "Notations après l'action",
        "composite": "Notation composite : jamais utilisée pour décider (ADR-001)",
        "facts": "1. Faits clés (passages verbatim)",
        "why": "2. Pourquoi c'est important pour le crédit",
        "indicators": "3. Indicateurs clés",
        "checks": "4. Points à vérifier par l'analyste",
        "rules": "5. Règles déclenchées",
        "sources": "6. Sources",
        "verification": "7. Vérification",
        "verified_line": (
            "Claims vérifiés : {verified} · Partiels : {partial} · Non supportés : {unsupported}"
        ),
        "all_backed": "Tous les claims factuels sont rattachés à une source",
        "unsupported_title": "Annexe : claims non supportés (exclus de la note de comité)",
        "unsupported_count": "Unsupported claims: {n}",
        "excluded": "excluded from the committee note",
        "source_fields": "source",
        "generated": "Générée le",
        "rules_version": "règles",
        "none": "aucune",
    },
    "en": {
        "title": "CREDIT COMMITTEE NOTE",
        "issuer": "Issuer",
        "priority": "Priority",
        "event": "Event",
        "date": "Effective date",
        "ratings": "Ratings after the action",
        "composite": "Composite rating: never used to decide (ADR-001)",
        "facts": "1. Key facts (verbatim passages)",
        "why": "2. Why it matters",
        "indicators": "3. Key indicators",
        "checks": "4. Points to verify",
        "rules": "5. Triggered rules",
        "sources": "6. Sources",
        "verification": "7. Verification",
        "verified_line": (
            "Verified claims: {verified} · Partial: {partial} · Unsupported claims: {unsupported}"
        ),
        "all_backed": "All factual claims source-backed",
        "unsupported_title": "Annex: unsupported claims (excluded from the committee note)",
        "unsupported_count": "Unsupported claims: {n}",
        "excluded": "excluded from the committee note",
        "source_fields": "source",
        "generated": "Generated on",
        "rules_version": "rules",
        "none": "none",
    },
}


def render_markdown(note: CommitteeNote) -> str:
    t = L[note.lang]
    src = SECTION_SOURCE[note.lang]
    lines = [f"# {t['title']}", ""]
    lines += [
        f"**{t['issuer']}**  ",
        f"{note.issuer_name} ({note.issuer_id}) · {note.universe_label}",
    ]
    lines += ["", f"**{t['priority']}**  "]
    lines += [f"{note.priority or 'NONE'} ({note.decision_status}) · {note.summary}", ""]
    lines += [f"**{t['event']}**  ", f"{note.title}"]
    lines += [f"{t['date']}: {note.effective_date or 'unknown'}", ""]
    if note.ratings_after:
        lines.append(f"**{t['ratings']}**  ")
        for r in note.ratings_after:
            extra = f", outlook {r.outlook}" if r.outlook else ""
            lines.append(
                f"{r.agency_name}: {r.rating} {r.category}{extra} "
                f"(as of {r.as_of.isoformat()}, {r.origin})  "
            )
        lines += [f"_{t['composite']}_", ""]
    lines += [f"## {t['facts']}", ""]
    for f in note.key_facts:
        field = f" ({f.field})" if f.field else ""
        lines.append(f'{f.index}. "{f.text}" [{t["source_fields"]} {f.source}]{field}')
    lines.append("")
    lines += [f"## {t['why']}", "", f"_{src[note.why_it_matters.source]}_", ""]
    lines += [f"- {c.text}" for c in note.why_it_matters.claims] or [f"- {t['none']}"]
    lines.append("")
    lines += [f"## {t['indicators']}", ""]
    lines += [f"- {i.label}: {i.value} ({i.source})" for i in note.indicators]
    lines.append("")
    lines += [f"## {t['checks']}", "", f"_{src[note.points_to_verify.source]}_", ""]
    lines += [f"- {c.text}" for c in note.points_to_verify.claims] or [f"- {t['none']}"]
    lines.append("")
    lines += [f"## {t['rules']}", ""]
    lines += [
        f"- {r.id} [{r.priority}] {r.description or ''}: {r.reason}" for r in note.triggered_rules
    ] or [f"- {t['none']}"]
    lines += [f"- ({t['rules_version']}.yaml {note.rules_version})", ""]
    lines += [f"## {t['sources']}", ""]
    for s in note.sources:
        if not s.available:
            lines.append(f"[{s.index}] {s.doc_id} (document not available)")
            continue
        lines.append(f"[{s.index}] {s.title or s.doc_id}  ")
        lines.append(f"    {s.url}  ")
        lines.append(
            f"    form {s.form or 'n/a'}, published {s.published or 'unknown'}, "
            f"retrieved {s.retrieved}  "
        )
        lines.append(f"    SHA-256 raw {s.raw_sha256}  ")
        lines.append(f"    SHA-256 normalised {s.normalized_sha256} ({s.normalizer_version})")
    lines.append("")
    v = note.verification
    lines += [f"## {t['verification']}", ""]
    lines.append(t["all_backed"] + "  ")
    lines.append(t["verified_line"].format(**v) + "  ")
    lines.append(t["unsupported_count"].format(n=v["unsupported"]))
    if note.model_provenance:
        mp = note.model_provenance
        how = "cached" if mp.get("cached") else "generated"
        lines.append(
            f"Prose: {mp.get('model_id')} (prompt {mp.get('prompt_version')}, {how}, "
            f"{mp.get('cost_usd', 0):.4f} USD)"
        )
    lines.append("")
    if note.unsupported:
        lines += [f"## {t['unsupported_title']}", ""]
        for c in note.unsupported:
            lines.append(f'- UNSUPPORTED · {t["excluded"]} · {c.reason}: "{c.text}"')
        lines.append("")
    if note.notice:
        lines += [f"_{note.notice}_", ""]
    lines.append(
        f"_{t['generated']} {note.generated_at.isoformat()} · note {note.note_id[:12]} · "
        f"alert {note.alert_id[:12]}_"
    )
    lines += ["", f"**{note.disclaimer}**", ""]
    return "\n".join(lines)


STYLE = (
    "body{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;max-width:900px;"
    "margin:0;padding:24px;color:#111827;background:#fff}h1{font-size:1.4em}"
    "h2{font-size:1.05em;margin-top:22px;border-bottom:1px solid #e5e7eb}p{margin:4px 0}"
    "li{margin-bottom:4px}code{background:#f3f4f6;padding:1px 4px}"
)


def render_html(note: CommitteeNote) -> str:
    """A self-contained page from the Markdown render: headings, paragraphs and lists only."""
    body: list[str] = []
    in_list = False
    for raw in render_markdown(note).splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if not line.strip():
            continue
        if line.startswith("# "):
            body.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{_inline(line[3:])}</h2>")
        else:
            body.append(f"<p>{_inline(line.rstrip(' '))}</p>")
    if in_list:
        body.append("</ul>")
    title = f"{note.priority or 'NONE'} {note.issuer_name}: {note.title}"
    head = (
        f'<html lang="{note.lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
    )
    return "<!DOCTYPE html>\n" + head + "\n" + "\n".join(body) + "\n</body></html>\n"


def _inline(text: str) -> str:
    """Escape, then the two Markdown marks the render uses (bold, italic)."""
    out = html.escape(text, quote=True)
    while "**" in out:
        out = out.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    if out.startswith("_") and out.endswith("_") and len(out) > 2:
        out = f"<em>{out[1:-1]}</em>"
    return out

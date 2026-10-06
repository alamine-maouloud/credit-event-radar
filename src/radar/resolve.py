"""Deterministic document to issuer resolution (docs/SPEC.md section 8, step 3).

Order of precedence: SEC CIK carried by the connector, then the issuer hint set by the
connector, then an exact alias match in the title and the head of the text. Anything
ambiguous or unknown stays ``unresolved``; nothing is ever guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from radar.config import Issuer, Universe
from radar.models import RawDocument

ResolutionMethod = Literal["cik", "hint", "alias", "unresolved"]
HEAD_CHARS = 3000


@dataclass(frozen=True)
class Resolution:
    issuer_id: str | None
    method: ResolutionMethod
    detail: str

    @property
    def resolved(self) -> bool:
        return self.issuer_id is not None


def _alias_regex(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)


def _aliases(issuer: Issuer) -> list[str]:
    names = [issuer.name, *issuer.aliases]
    if issuer.legal_entity:
        names.append(issuer.legal_entity)
    return sorted(set(names), key=len, reverse=True)


def resolve_by_alias(text: str, universe: Universe) -> Resolution:
    matched: dict[str, str] = {}
    for issuer in universe.issuers:
        for alias in _aliases(issuer):
            if _alias_regex(alias).search(text):
                matched[issuer.id] = alias
                break
    if len(matched) == 1:
        issuer_id, alias = next(iter(matched.items()))
        return Resolution(issuer_id, "alias", f"alias {alias!r}")
    if matched:
        return Resolution(None, "unresolved", f"ambiguous aliases: {', '.join(sorted(matched))}")
    return Resolution(None, "unresolved", "no alias match")


def resolve_document(doc: RawDocument, universe: Universe) -> Resolution:
    cik = doc.extra.get("cik")
    if isinstance(cik, str) and cik.strip():
        cik = cik.strip().zfill(10)
        issuers = [i for i in universe.issuers if i.sec_cik == cik]
        if len(issuers) > 1:
            return Resolution(None, "unresolved", f"cik {cik} shared by several issuers")
        if not issuers:
            return Resolution(None, "unresolved", f"cik {cik} not in universe")
        issuer = issuers[0]
        if doc.issuer_hint and doc.issuer_hint != issuer.id:
            return Resolution(
                None, "unresolved", f"hint {doc.issuer_hint} disagrees with cik {cik} ({issuer.id})"
            )
        return Resolution(issuer.id, "cik", f"cik {cik}")
    if doc.issuer_hint:
        if doc.issuer_hint in universe.ids:
            return Resolution(doc.issuer_hint, "hint", "connector hint")
        return Resolution(None, "unresolved", f"hint {doc.issuer_hint} not in universe")
    head = "\n".join(part for part in (doc.title or "", doc.text[:HEAD_CHARS]) if part)
    return resolve_by_alias(head, universe)

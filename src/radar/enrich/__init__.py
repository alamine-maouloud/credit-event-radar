"""Lot 3.3b: validated LLM statements enrich existing events, the rules decide again.

Extraction (radar.enrich.extract) and application (radar.enrich.apply) are separate steps:
the first stores statements with their validation, the second builds a GuidanceEnrichment
(radar.enrich.guidance) and applies it to an event without overwriting any deterministic
field, idempotently and with an audit of the priority before and after.
"""

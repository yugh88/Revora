"""Revora decision engine.

Deterministic, in-house reasoning only — no LLM, no external AI API anywhere in
this package (BUILD_SPEC hard constraint). The one machine-learning component in
the system lives in app/ml/ and is an independent check, never an authority over
the action taken.
"""

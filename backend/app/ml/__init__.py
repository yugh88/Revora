"""Hybrid ML layer. BUILD_SPEC Section 4a.

One self-trained shallow decision tree, trained offline on our own synthetic
data. No LLM, no external AI API, no second model. The rule engine in
app/engine/diagnosis_engine.py remains authoritative for the action taken; this
layer is an independent check that surfaces disagreement for human review.

Non-blocking by design: if the model is missing or predict() raises, the
pipeline logs it and continues on deterministic diagnosis alone.
"""

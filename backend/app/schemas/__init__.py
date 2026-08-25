"""Pydantic request/response schemas, mirroring app/models/.

These are the API contract. Models are the storage contract; keeping them
separate means a column can change shape without silently changing what the
frontend receives.
"""

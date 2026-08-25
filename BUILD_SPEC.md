# Revora — Build Spec
### Razorpay Buildathon — Track 03: AI Revenue Recovery

> **SCOPE FROZEN as of this version.** No LLM, no TTS, no LangChain/LangGraph, no Redis, no Kafka, no PostgreSQL, no other AI API, no extra dashboards, no extra event types beyond the 5 core ones, no unnecessary microservices, no ML models beyond the one diagnosis classifier, no "cool" features not already in this doc. The remaining 10 days are for building this exactly, not expanding it.
>
> **Golden rule:** don't optimize for feature count. Optimize for `detect → diagnose → decide → policy → execute → verify → recover/escalate → audit → batch metrics` working flawlessly, end to end. That loop, done reliably, is already a strong submission.


Single source of truth for the build. Paste the relevant section(s) into each Opus session so context isn't lost between sessions — Opus has no memory of prior sessions except what's in this doc and the repo itself. Timeline: ~10-11 days, 2-3 hrs/day.

**Hard constraint: no LLM APIs, no paid third-party APIs, anywhere in the core product.** All reasoning, scoring, and text generation (including Hinglish scripts) is built in-house with deterministic rules/templates. The only external service touched at all is Razorpay's own free test-mode sandbox, and only as one of two user-selectable simulation modes (see Section 5).

---

## 1. What we're building

An agent that detects revenue at risk, diagnoses the root cause, picks a bounded/explainable intervention, executes it, tracks the outcome, and produces an audit trail — across **5 core event types**, with B2B receivables, Promise-to-Pay, and Hinglish voice recovery implemented as **workflows/capabilities on top of the same engine**, not separate systems.

**The bar — every one of these must be demonstrably true, not asserted:**
- Measured money recovered across a batch (real numbers from ledger state, not invented)
- Compliant escalation with a hard ceiling
- Stopping rules that actually stop things
- Full audit trail: detection → diagnosis → decision → policy → execution → verification → recovery/escalation

---

## 2. Scope tiers

**P0 — must be built correctly, this is the actual product:**
CustomerProfile, PaymentAttempt, ActionLock, structured `Decision` (decision_factors/recovery_probability/policy_result/policy_version), deterministic Recovery Probability Engine, **self-trained ML diagnosis classifier (hybrid symbolic+ML — see Section 4a)**, template-based reasoning/script generation (no LLM), `/batch`, `/policies`, idempotency, gateway abstraction (LocalSimulationGateway + RazorpayTestGateway, user-toggleable), state machine with enforced terminal states, race-condition re-check before execution, batch fault isolation, structured JSON logging + correlation IDs, `.env` secrets, `/exceptions`, `/audit`.

**P1 — build if schedule holds:**
Simple ML model for recovery-probability scoring too (in addition to diagnosis), as a second toggleable probability source.

**P2 — explicitly out of scope, one paragraph in README explaining why:**
Full eval/observability dashboards, CI pipeline, Redis/Kafka/Postgres/cloud infra.

---

## 3a. Why this is agentic AI, not generative/LLM-based AI (put this near the top of the README)

Agentic AI means perceive → reason → decide → act → monitor → adapt, autonomously and within bounds. That's this system: RiskEvent ingestion (perceive), diagnosis+probability+policy engines (reason), gateway execution (act, but bounded by ActionLock/idempotency/stopping rules), outcome tracking + promise watcher (monitor/adapt). None of that requires an LLM — LLM-orchestrated agents are one implementation of agentic AI, not the definition.

This system is deliberately **not** generative AI: no LLM calls anywhere, by requirement. For a system making bounded money decisions, that's a defensible design choice, not a limitation — every decision traces to a named rule or a specific model prediction, not to a model's free-text generation.

It **is** AI/ML: a hybrid symbolic + machine-learning architecture (Section 4a) — rules remain the authority for what action gets taken (auditable, bounded, explainable), while a self-trained classifier runs as an independent check that surfaces disagreement for human review rather than silently overriding or being ignored. That combination — not just rules, not just ML — is the actual technical story.

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python + FastAPI, Pydantic models, SQLAlchemy |
| DB | SQLite, file persisted via Docker volume |
| Scheduling | APScheduler (in-process) |
| Frontend | Next.js 14 (App Router) + Tailwind + shadcn/ui + Recharts |
| Text generation | In-house template engine (Jinja2-style templates stored in YAML config files, filled from `decision_factors`) — no LLM, no paid API |
| Payment integration | `razorpay-python` SDK, FREE test-mode keys, only inside `RazorpayTestGateway`, only if the user selects that mode at runtime |
| Deploy | Docker Compose: `api` + `web` services, shared volume for SQLite. No Redis/Kafka/Postgres. |

---

## 4. Data model

### Merchant
```
id (PK), name, created_at
```
`RiskEvent.merchant_id` (already defined below) and `Policy.merchant_id` (added below) both FK to this — policies and events belong to a merchant, not global.

### RiskEvent (5 core types only)
```
id, type [payment_degraded|checkout_abandoned|subscription_failed|invoice_overdue|mandate_failed],
merchant_id, customer_id, amount, currency, source_ref, detected_at,
raw_signal (JSON), status [open|diagnosing|intervening|recovered|escalated|unrecoverable|stopped],
gateway_used [local_simulation|razorpay_test], correlation_id
```
B2B receivables = `invoice_overdue` with a `channel=b2b` flag. Promise-to-Pay = a state tracked on `invoice_overdue`/`subscription_failed` events (broken promise re-enters the engine as a new event, root cause `broken_ptp`). Hinglish = an execution channel (`voice_script`), not an event type.

### CustomerProfile
```
customer_id (PK), payment_success_rate, payment_failure_rate, lifetime_value,
avg_payment_delay_days, preferred_channel, do_not_contact (bool),
created_at, updated_at
```

### PaymentAttempt
```
id, event_id (FK), attempt_number, status [pending|success|failed|timeout],
failure_reason, idempotency_key (UNIQUE), provider_ref,
gateway_used [local_simulation|razorpay_test], initiated_at, resolved_at
```

### Diagnosis
```
event_id, root_cause_code, confidence, evidence (JSON array), diagnosed_at
```

### MLDiagnosisPrediction (Section 4a — self-trained classifier, the hybrid AI/ML layer)
```
event_id (FK), predicted_root_cause, confidence, agrees_with_rule_engine (bool),
model_version, predicted_at
```
- Model: shallow Decision Tree (scikit-learn, `max_depth` ~4-6) — chosen for explainability (the tree itself is a visual artifact you can show in the demo), not just accuracy.
- Features: amount, attempt_number, days_since_event, gateway_error_code (encoded), customer_success_rate, event_type, time_of_day.
- Trained offline on your own labeled synthetic data (the generator already assigns ground-truth root causes) — `backend/app/ml/train_diagnosis_model.py`, run once/as needed, saves `diagnosis_classifier.joblib` + `metrics.json` (precision/recall/confusion matrix on a held-out test split — real numbers, reported honestly, not asserted).
- **Wiring into the pipeline**: rule-based `diagnosis_engine` remains authoritative for the action actually taken (safety/auditability). The classifier runs independently on every event; if `predicted_root_cause != rule_engine_root_cause` OR `confidence` is below a threshold, the event is flagged and routed into `/exceptions` as "ML/rule disagreement — needs review" instead of silently overridden or ignored. `/batch` reports `ml_agreement_rate` as a real measured stat.
- `GET /ml/metrics` — exposes the held-out precision/recall/confusion matrix from the last training run.
- **Non-blocking by design**: if the classifier fails or is unavailable (model file missing, exception during `predict()`), the pipeline logs it and continues on the deterministic rule-based diagnosis alone — ML is an enhancement layer, never a dependency the core loop can be broken by.

### Decision (structured reasoning — source of truth, template-rendered, no LLM)
```
event_id, decision_factors (JSON: root_cause, confidence, amount, customer_success_rate,
  attempt_number, channel_preference, days_overdue...),
recovery_probability (float), probability_source [deterministic|ml_p1],
policy_result (structured JSON: {status: allowed|blocked, rule_triggered, threshold_checked, actual_value, threshold_value}),
policy_version, action_code, reasoning_text (rendered from decision_factors via template engine),
decided_at
```

### ActionLock (concurrency protection)
```
event_id (PK, unique), locked_by, locked_at, expires_at (TTL)
```
Any job must acquire this lock before acting on an event. Expired locks are reclaimable (handles crashed jobs).

### StoppingRuleState
```
event_id, attempts_used, max_attempts_for_type, cooldown_until,
do_not_contact_snapshot, escalation_level, hard_stop_reason (nullable)
```

### Policy (merchant-configurable, drives `/policies`)
```
policy_version, merchant_id (FK), event_type, max_attempts, cooldown_hours, amount_threshold,
recovery_probability_threshold, contact_limit_per_channel, escalation_ceiling,
updated_at
```

### Outcome / RecoveryLedger
```
event_id, resolved [recovered|partially_recovered|lost|pending],
amount_recovered, resolved_at, resolution_channel
```

### PromiseToPay (Section on Promise-to-Pay tracker — must be a real tracked entity, not just an implied state)
```
id, event_id (FK, on invoice_overdue or subscription_failed),
promised_date, promised_amount, status [pending|kept|broken],
created_at, resolved_at
```
A daily APScheduler job (`engine/promise_tracker.py`) checks all `pending` promises: if `promised_date` has passed and the underlying event is still unresolved, mark `broken`, and create a NEW `RiskEvent` with root cause `broken_ptp` (tone escalates one level above where the original conversation left off) — this is what makes the tracker a real closed loop, not just a label.

### AuditLog (immutable, append-only)
```
timestamp, event_id, correlation_id, actor [system|human],
stage [detection|diagnosis|decision|policy|execution|verification|recovery|escalation],
action, before_state, after_state, reasoning
```

---

## 5. Payment simulation — dual mode, user-selectable at runtime

Two gateways, both implementing the same interface:

```
PaymentGateway (interface): initiate_retry(), check_status(), cancel()
LocalSimulationGateway(PaymentGateway)   → fully self-built, deterministic synthetic responses, zero external deps
RazorpayTestGateway(PaymentGateway)      → wraps razorpay-python SDK, Razorpay's own FREE test-mode sandbox
```

**This is exposed as a visible toggle in the UI** (on `/batch` and `/events` pages), not buried in config — e.g. a switch labeled *"Simulate with: Built-in Simulator | Razorpay Test Sandbox"*. Default = Built-in Simulator.

Why both, and why show the toggle to the demo audience:
- **Built-in Simulator** proves the product is self-sufficient and works with zero setup, zero credentials, zero external dependency — this is the one that must never fail during judging.
- **Razorpay Test Sandbox** (free, official, not a paid third party) proves real integratability with Razorpay's own systems, without violating "build it yourself" — the decisioning, diagnosis, scoring, and templates are 100% yours either way; only the payment execution call differs.
- **Important:** `LocalSimulationGateway` must independently replicate Razorpay's real subscription behavior (auto-retry once the next day, then move to `halted` if that fails too) — not just generic retry logic. Without this, the failed-subscription-recovery direction would only be demonstrable with the Razorpay toggle on, which breaks the "self-sufficient by default" requirement.
- Letting the *user* flip this switch live during the demo is itself a nice moment: "here's the exact same engine, same audit trail, same policy gate — now watch it call Razorpay's real sandbox instead."

---

## 6. Root-cause vocabulary + intervention table

| Event type | Root causes | Attempt 1 | Attempt 2 | Escalation | Hard stop |
|---|---|---|---|---|---|
| payment_degraded | card_expired, insufficient_funds, issuer_declined (hard), network_timeout, 3ds_failed, bank_server_down, risk_engine_blocked | Update-card email (soft causes) | SMS reminder | Human handoff if amount > threshold | issuer_declined → **no retry, immediate stop**; else after 2 attempts |
| checkout_abandoned | payment_step_dropped, otp_timeout, price_shock, no_preferred_method, session_expired, unknown | In-app nudge (same session) | Email w/ saved-cart link (max 1) | — | after 1 email |
| subscription_failed | card_expired, insufficient_funds, mandate_revoked, user_paused, halted_after_max_retries | React to Razorpay's own auto-retry/webhook state — do not force extra retries | — | — | on `halted` |
| invoice_overdue (incl. B2B) | forgotten, disputed_amount, awaiting_approval, cash_flow_delay, delivery_failure, broken_ptp | <7d: friendly reminder | 7-30d: reminder + call script, escalation L1 | >30d: formal notice, human handoff, escalation L2 (**ceiling**) | never auto-escalate past L2 |
| mandate_failed | not_authenticated, insufficient_balance, bank_rejected, expired, revoked | **Real sequence, not generic 2-attempt:** Day+1 immediate re-auth nudge (not_authenticated) or retry (insufficient_balance) | Day+3 retry, timed to likely salary-credit window for insufficient_balance | Day+7 final retry attempt, escalation L1 if still unresolved | hard stop after Day+7 attempt; bank_rejected (hard reason) → no retry, immediate stop, same logic as issuer_declined |

Recovery Probability Engine (deterministic, P0):
```
score(action) = P(recovery | root_cause, action, attempt_number) × amount_at_risk
                − cost(action) − annoyance_penalty(attempt_number)
```
`P(...)` starts as a hand-set lookup table you define yourself (documented in `probability_engine.py`). Pick highest-scoring action that passes the policy gate. P1 stretch: swap in a small logistic regression you train yourself on synthetic outcomes, toggleable against the deterministic score — never an external ML API.

---

## 7. Text generation — template engine, no LLM

`reasoning_text` and Hinglish scripts are rendered from `decision_factors` using your own template engine — never a hand-typed string, never an LLM call:

- Templates live in `backend/app/templates/*.yaml` (never hardcoded inline in logic — same principle as "prompt isolation," just for templates)
- Each template keyed by `(event_type, root_cause, escalation_level)` → a sentence skeleton with slots: `{customer_name}`, `{amount}`, `{days_overdue}`, `{attempt_number}`, `{tone}`
- Hinglish scripts additionally carry `tone` (friendly/neutral/formal), `urgency` (low/medium/high mapped from escalation_level), and a `compliance_validation` flag against a **named, concrete rule set** (document this explicitly in `compliance_rules.yaml`, modeled on RBI Fair Practices Code norms for collections communication): (1) no contact-time restriction violations — scripts are only ever generated for a "contact between 8am-7pm IST" context, never framed as calls outside that window; (2) no coercive or threatening language — a blocklist of phrases ("legal action," "will be reported," "blacklisted") that auto-fail validation unless explicitly authorized at escalation L2+ with accurate, non-exaggerated wording; (3) frequency cap enforcement — script generation itself checks `StoppingRuleState.attempts_used` against the policy's `contact_limit_per_channel` before rendering; (4) no false urgency — urgency level must match the actual `escalation_level`, never inflated. Every generated script logs which rules it was checked against to AuditLog, not just a pass/fail bit.
- This is genuinely more auditable than an LLM for a fintech judging panel — every generated sentence traces back to an exact template + exact facts, no hallucination risk

---

## 8. State machine

```
open → diagnosing → intervening → { recovered | escalated | unrecoverable | stopped }
```
`recovered` and `unrecoverable` are **terminal** — any attempted transition out of them must be rejected AND logged to AuditLog as a caught anomaly.

---

## 9. Race conditions, idempotency, fault isolation

- **Before executing** any recovery action: re-check current upstream state (payment/subscription/invoice, from whichever gateway is active). If already resolved/cancelled/paid externally → stop immediately, log `recovered_externally`, do not double-act.
- **Idempotency**: every execution request carries a key (hash of event+attempt); check `PaymentAttempt` for existing row before executing — return existing result if found, never re-execute.
- **Batch fault isolation**: per-record try/except in `/batch`; one bad row → caught, logged, batch continues (target: e.g. 499 processed + 1 isolated exception, never a batch crash).
- **Named failure scenarios to explicitly handle**: timeout, duplicate request, unknown failure, already-recovered payment, invalid/malformed data, policy rejection, max-retries-hit, cooldown violation, template-render failure, gateway failure.

---

## 10. API surface

- `POST /events` — ingest a risk event (webhook or synthetic)
- `GET /events`, `GET /events/{id}` — feed + drill-down (diagnosis, decision, stopping-rule state, audit timeline)
- `POST /batch` — process N synthetic records (default 50, supports 500), `gateway` param [local_simulation|razorpay_test]; returns amount at risk/attempted/recovered/lost + recovery rate (from actual ledger state), **plus explicit breakdowns that directly prove the bar**: `escalation_ceiling_hits` (count stopped from escalating further), `stopping_rule_triggers` (broken down by reason: cooldown, do_not_contact, max_attempts, hard_decline), `promises_made` / `promises_kept` / `promises_broken`
- `GET /exceptions` — unresolved/low-confidence cases + why the engine chose not to act
- `GET /policies`, `PUT /policies` — merchant-configurable thresholds
- `GET /audit` — searchable immutable log
- `GET /scripts/{event_id}` — Hinglish script + reasoning + tone + urgency + compliance validation
- `GET /ml/metrics` — held-out precision/recall/confusion matrix for the diagnosis classifier (Section 4a)

---

## 11. Synthetic data generator — inject deliberately, at these rates

- Even spread across the 5 event types
- Indian context: INR, UPI/NACH/card mix, IST timestamps, real Razorpay error-code vocabulary
- ~10% duplicate/replayed events (idempotency test)
- ~8% missing/malformed fields (validation test)
- ~10% already-resolved-externally (race-condition test)
- ~10% hard-decline (gating test — must NOT retry)
- ~5% ambiguous root cause → low-confidence bucket, not force-classified
- A few multi-event customers (per-customer cooldown/do-not-contact scoping test)

A 100% resolution rate on the batch is a red flag, not a win.

**Note:** the percentages above are independent probabilities checked per record, not mutually exclusive buckets — a single record can land in more than one category (e.g. a duplicate that's also malformed). Use a **fixed random seed** (e.g. `42`) in the generator so batch runs are reproducible across demo runs and across your own repeated testing.

---

## 12. FULL DIRECTORY MANIFEST — Opus must create and maintain exactly this structure

Give this section to every Opus session. If a new file seems needed, add it here first, then create it — never create ad hoc files outside this manifest, or later sessions will drift and duplicate logic.

```
revora/
├── README.md
├── BUILD_SPEC.md                        # this document, kept in repo
├── .gitignore
├── docker-compose.yml
│
├── backend/
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .env.example                     # RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET (test mode), DATABASE_URL
│   ├── app/
│   │   ├── main.py                      # FastAPI app, mounts all routers, correlation-id middleware
│   │   ├── config.py                    # settings via pydantic-settings, reads .env
│   │   ├── database.py                  # SQLAlchemy engine/session
│   │   ├── models/                      # SQLAlchemy ORM — one file per table
│   │   │   ├── merchant.py
│   │   │   ├── risk_event.py
│   │   │   ├── customer_profile.py
│   │   │   ├── payment_attempt.py
│   │   │   ├── diagnosis.py
│   │   │   ├── decision.py
│   │   │   ├── action_lock.py
│   │   │   ├── stopping_rule_state.py
│   │   │   ├── policy.py
│   │   │   ├── outcome.py
│   │   │   ├── promise_to_pay.py
│   │   │   └── audit_log.py
│   │   ├── schemas/                     # Pydantic request/response, mirrors models/
│   │   │   └── (one file per model, same names as above, + batch.py)
│   │   ├── gateways/
│   │   │   ├── base.py                  # PaymentGateway ABC
│   │   │   ├── local_simulation.py
│   │   │   └── razorpay_test.py
│   │   ├── engine/
│   │   │   ├── diagnosis_engine.py      # root-cause classification, Section 6 table
│   │   │   ├── probability_engine.py    # deterministic scoring, Section 6 formula
│   │   │   ├── policy_engine.py         # policy gate against Policy table
│   │   │   ├── decision_engine.py       # orchestrates diagnosis+probability+policy → Decision
│   │   │   ├── state_machine.py         # transition validation, Section 8
│   │   │   ├── locks.py                 # ActionLock acquire/release/TTL reclaim
│   │   │   ├── idempotency.py           # key generation + check
│   │   │   ├── promise_tracker.py       # PromiseToPay daily watcher job, Section 4
│   │   │   └── template_engine.py       # renders reasoning_text + Hinglish scripts, checks compliance_rules.yaml, Section 7
│   │   ├── routers/                     # one file per API group, Section 10
│   │   │   ├── events.py
│   │   │   ├── batch.py
│   │   │   ├── exceptions.py
│   │   │   ├── policies.py
│   │   │   ├── audit.py
│   │   │   ├── scripts.py
│   │   │   └── ml.py                    # /ml/metrics, Section 4a
│   │   ├── ml/                          # hybrid ML layer, Section 4a — self-trained, no LLM/API
│   │   │   ├── train_diagnosis_model.py # offline training script
│   │   │   ├── diagnosis_classifier.py  # loads model, exposes predict()
│   │   │   └── models/
│   │   │       ├── diagnosis_classifier.joblib
│   │   │       └── metrics.json         # precision/recall/confusion matrix, held-out test set
│   │   ├── services/
│   │   │   ├── synthetic_data_generator.py   # Section 11
│   │   │   └── logging_config.py             # structured JSON logs + correlation id
│   │   └── templates/                        # YAML template configs, NOT python code
│   │       ├── reasoning_templates.yaml
│   │       ├── hinglish_script_templates.yaml
│   │       └── compliance_rules.yaml    # contact-time, blocklist, frequency-cap, urgency rules — Section 7
│   └── tests/
│       ├── test_state_machine.py
│       ├── test_local_simulation_gateway.py
│       ├── test_synthetic_data_generator.py
│       ├── test_diagnosis_engine.py
│       ├── test_probability_engine.py
│       ├── test_decision_engine.py
│       ├── test_idempotency.py
│       ├── test_policy_engine.py
│       ├── test_batch.py
│       ├── test_batch_fault_isolation.py
│       ├── test_exceptions.py
│       ├── test_audit.py
│       └── test_logging_config.py
│
└── frontend/
    ├── package.json
    ├── Dockerfile
    ├── next.config.js
    ├── tailwind.config.js
    ├── .env.local.example                    # NEXT_PUBLIC_API_URL
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx                          # "/" dashboard, Section 13 item 1
    │   ├── events/
    │   │   ├── page.tsx                      # "/events" feed
    │   │   └── [id]/page.tsx                 # drill-down
    │   ├── batch/page.tsx                    # trigger batch run, gateway toggle lives here
    │   ├── exceptions/page.tsx
    │   ├── audit/page.tsx
    │   ├── scripts/page.tsx
    │   └── policies/page.tsx
    ├── components/
    │   ├── ui/                               # shadcn/ui generated components
    │   ├── KpiCard.tsx
    │   ├── RecoveryChart.tsx
    │   ├── DirectionBreakdown.tsx
    │   ├── EventTable.tsx
    │   ├── EventDrilldown.tsx
    │   ├── StatusBadge.tsx
    │   ├── AuditTimeline.tsx
    │   ├── PolicyForm.tsx
    │   ├── GatewayToggle.tsx                 # Section 5 — the built-in-vs-Razorpay switch
    │   └── SkeletonLoader.tsx
    └── lib/
        ├── api-client.ts                     # fetch wrapper to FastAPI
        └── types.ts                          # TS types mirroring backend schemas
```

**Rule for Opus:** at the start of every session, paste this manifest plus the relevant spec section(s), and instruct it to only touch files listed here (adding new ones to the manifest first if genuinely needed). This is what keeps 10+ separate sessions from drifting into inconsistent code.

---

## 13. Frontend pages — what each shows

1. `/` — KPI cards (at risk / recovered / recovery rate / active interventions) + recovery-over-time chart + by-direction breakdown
2. `/events` — filterable feed → drill-down panel (diagnosis, decision + score, stopping-rule state, audit timeline)
3. `/batch` — run a batch (50 or 500), gateway toggle (Section 5), results summary
4. `/exceptions` — honest unresolved list with "why we didn't act"
5. `/audit` — raw searchable immutable log
6. `/scripts` — Hinglish script viewer with reasoning/tone/urgency/compliance flag
7. `/policies` — merchant config form

Consistent status colors (amber=pending, green=recovered, red=unrecoverable, gray=stopped) across every page. Skeleton loaders, not blank screens.

---

## 14. Git workflow — when to commit, when to push, when to Docker

- **Before session 1**: `git init`, create `.gitignore` (`.env`, `.env.local`, `venv/`, `node_modules/`, `*.db`, `__pycache__/`), create the GitHub repo, first commit = "project scaffold + BUILD_SPEC.md", push.
- **After every meaningful chunk within a session** (not just once at the end of a 2-3h session) — commit with a specific message tied to what was built, e.g. `feat: RiskEvent + CustomerProfile models`, `feat: deterministic probability engine`, `fix: idempotency check on duplicate webhook`. A clean, incremental commit history across 10 days is itself evidence to the panel that this was actually built over time, not assembled the night before — don't squash it away.
- **Push at the end of every session**, no exceptions — protects you against a bad session losing work, and keeps the repo demo-able at any point if you run out of time early.
- **Dockerfiles**: write skeleton `Dockerfile`s for both `backend` and `frontend` as soon as each app runs locally (roughly session 2 for backend, session 6 for frontend) — don't wait until the end. Run `docker build` on each at least once every 2-3 sessions so failures surface early, not on the last day.
- **`docker-compose.yml`**: write once both Dockerfiles exist (around session 6-7), do a full `docker compose up` integration test then, and again after any major schema/API change.
- **Final days (10-11)**: full `docker compose up` from a clean clone (simulates what the judges will actually do), fix anything that only breaks in a fresh environment, final commit, tag it (`git tag submission-v1`), push.

---

## 15. Suggested session breakdown (10-11 days × 2-3h) — files touched per session

1. **Models → Database → Enums → Relationships → State machine → State-machine tests**: `backend/app/models/*` (incl. `merchant.py`), `backend/app/database.py`, enums for all status/type fields, FK relationships wired, `backend/app/engine/state_machine.py`, `backend/tests/test_state_machine.py`. → git init, first commit/push.
2. **Local gateway → Synthetic data → Edge cases**: `backend/app/gateways/base.py` + `local_simulation.py`, `backend/app/services/synthetic_data_generator.py` (fixed seed, overlapping edge-case injection per Section 11), verify edge cases actually trigger. Write backend `Dockerfile` skeleton, test build once.
3. **Decision pipeline + ML classifier**: `backend/app/engine/diagnosis_engine.py`, `probability_engine.py`, `policy_engine.py`, `decision_engine.py`, `locks.py`, `idempotency.py`, plus `backend/app/ml/train_diagnosis_model.py` + `diagnosis_classifier.py` (scikit-learn, add to `requirements.txt`) — train on synthetic data, wire agreement-check into `decision_engine.py`, save `metrics.json`.
4. **Batch + exceptions + audit**: `backend/app/routers/batch.py`, `exceptions.py`, `audit.py`, `backend/app/services/logging_config.py`. Full-batch dry run on synthetic data.
5. **Razorpay sandbox integration**: `backend/app/gateways/razorpay_test.py`, `.env.example`. Test both gateway modes side by side.
6. **Next.js scaffold**: `frontend/` init, `app/layout.tsx`, `app/page.tsx`, `lib/api-client.ts`, `lib/types.ts`. Write frontend `Dockerfile`.
7. **Events + batch pages**: `app/events/*`, `app/batch/page.tsx`, `components/GatewayToggle.tsx`, `EventTable.tsx`, `EventDrilldown.tsx`. Write `docker-compose.yml`, first full integration test.
8. **Policies + audit + template engine**: `backend/app/templates/*.yaml`, `backend/app/engine/template_engine.py`, `backend/app/routers/scripts.py`, `app/scripts/page.tsx`, `app/policies/page.tsx`, `app/audit/page.tsx`.
9. **End-to-end batch run** (50→500), fix edge cases surfaced, visual polish pass.
10. **Clean-clone Docker test, README.md, recorded walkthrough**, final tag + push.
11. **Buffer.**

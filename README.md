<div align="center">

# Revora

**An autonomous revenue recovery agent for Indian businesses.**

Razorpay Buildathon · Track 03 — AI Revenue Recovery

</div>

---

## Situation

Revenue rarely disappears loudly.

A customer's card expires and a subscription lapses silently. A checkout stalls at the
payment step and nobody follows up. An invoice ages past its due date while everyone
assumes someone else is chasing it. A mandate fails and the next three charges never
happen.

Individually each case is small enough to ignore. Together they are the quietest line
item on a P&L — and the one nobody owns. Chasing them by hand costs more than most of
them are worth, so they go unchased. Chasing them indiscriminately costs goodwill, which
is worse.

## Task

Build something that works these cases the way a careful person would, at a scale a
person cannot:

- Find revenue at risk without being told to look
- Work out **why** each case failed, because the right action depends on the cause
- Decide what is worth doing, weighing the money against the cost and the customer's patience
- Stay inside limits the merchant sets — always, with no exceptions and no appeals
- Speak to customers in language they actually use
- Track what they promise, and honour it
- Record everything, so any decision can be explained months later

And the harder requirement: **never lie about money.** Not to flatter a demo, not to fill
a dashboard, not by rounding.

## Action

### It runs on its own
A background task processes payment events as they arrive, through the real
pipeline — detect, diagnose, decide, gate, act, verify. The dashboard updates itself.

### The rules that never bend

**Policy is authoritative.** Scoring proposes, policy disposes. A high-scoring action the merchant forbids does not happen, and no model, context, or retry logic can appeal it.

**Humans remain in control.** Actions that cross a defined approval threshold do not execute autonomously. The system pauses, presents the proposed action with its supporting evidence, and requires explicit human approval before anything consequential happens. No approval means no execution.

**Money is counted once.** One function records a rupee as recovered. Amounts are integer paise, serialised as exact decimal strings, never floats. recovered + in progress + written off always equals the amount at risk, to the paisa — asserted by test.

**Compliance is a gate, not a warning.** A message failing any check is not written at all. There is no draft to override and no text to copy out.

**Nothing is invented.** No promised date the customer did not state, no delivery receipt, no figure the ledger cannot support. “I’ll pay soon” records intent and no date — guessing one would create a commitment nobody made.

**Every consequential action is explainable.** The system records what it proposed, why it proposed it, which policy allowed it, what evidence supported it, and whether a human approved it. The resulting state is auditable rather than inferred.

### Reports you can actually file

Recovery and audit reports are generated from Revora's authoritative data rather than
from a browser screenshot or dashboard estimate.

From **Audit** and **All Recoveries**, a merchant can choose:

- Last 1 day
- Last 1 week
- Last 1 month
- A custom date range

Revora generates a complete PDF containing the selected period, recovery or audit
summary, relevant cases, customer names, amounts, statuses, recovery actions,
diagnosis and reasoning, outcomes, timestamps, and audit decisions.

The report is generated server-side from the same data used by the dashboard, so the
document remains consistent with the system's actual recovery state.

Empty selections produce a clean report stating that no records were found rather than
fabricating results.

### Where the AI is, and is not

| Component | Role | Authority |
|---|---|---|
| **Rule-based diagnosis** | Determines root cause from deterministic signals | **Authoritative** |
| **Decision tree classifier** | Provides a recorded second opinion | **Advisory** |
| **Probability engine** | Ranks policy-eligible actions by expected recovery value | **Proposes only** |
| **Policy engine · stopping rules** | Enforces merchant limits, eligibility, and hard stops | **Authoritative** |
| **Human approval gate** | Reviews and approves consequential actions above the autonomy threshold | **Execution authority** |
| **Retrieval (RAG)** | Supplies customer history and supporting evidence | **Context only** |
| **LangGraph** | Manages workflow state, transitions, retries, and orchestration | **Orchestration only** |
| **Ollama · Mistral** | Rewrites approved copy into Hinglish without changing intent or facts | **Wording only** |
| **Financial ledger** | Records recovery state using exact integer-paisa accounting | **System of record** |

---

## Authority Model

```text

Customer Context

      │

      ▼

Rule-Based Diagnosis

      │

      ▼

Decision Tree

Second Opinion

      │

      ▼

Probability Engine

Rank Actions

      │

      ▼

Policy Engine

Hard Constraints

      │

      ├──────── Forbidden ────────► STOP

      │

      ▼

Human Approval Gate

      │

      ├──────── Rejected ─────────► STOP

      │

      ▼

Compliance Gate

      │

      ├──────── Failed ───────────► STOP

      │

      ▼

Approved Action

      │

      ▼

Execution

      │

      ▼

Financial Ledger
```
The language model receives a script compliance has already approved, and its output is
re-checked before use. If it is slow, offline, or invents a fact, the deterministic
template is used unchanged. **A recovery run cannot fail because of it.**

## Result

```
1,213 backend tests passing
```

The tests that matter most assert what the system **cannot** do:

- the ledger partition balances to the paisa
- a blocked message carries no text
- the ML classifier cannot change a decision
- a hostile customer reply changes no policy verdict
- retrieval never returns another customer's history
- the language model cannot reach the decision engine
- fulfilling a promise twice does not recover twice

The suite needs no network, no Ollama and no Redis. Real-service tests skip explicitly
rather than pass silently.

**The closed loop works end to end**, verified against a live stack:

```
20 communications → 14 replies → 8 promises
"Main kal payment kar dunga"        → tracked for tomorrow
"3 September tak payment kar dunga" → tracked for 3 Sep
promise open    → further contact BLOCKED
promise overdue → recovery resumes automatically
```

---

## Quick start

**Requirements:** Python 3.12+, Node.js 20+. Optionally [Ollama](https://ollama.com) for
Hinglish and Redis for locking — both genuinely optional.

```bash
docker compose up --build          # Everything at once
```

```bash
# Backend
cd backend
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install && npm run dev
```

Open `http://localhost:3000`. Recovery starts on its own within seconds.

### Tests

```bash
cd backend  && PYTHONPATH=. pytest -q
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```

### Configuration

Everything has a working default; nothing below is required.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./revora.db` | Where the ledger lives |
| `DEFAULT_GATEWAY` | `local_simulation` | Or `razorpay_test` |
| `RAZORPAY_KEY_ID` / `_SECRET` | – | **Test keys only** — rejected unless `rzp_test_` |
| `LLM_ENABLED` | `true` | Hinglish rewriting; falls back to templates |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama |
| `OLLAMA_LOCAL_MODEL` | `mistral:latest` | Local model |
| `REVORA_REDIS_ENABLED` | `1` | Set `0` to run without Redis |
| `AUTONOMOUS_RECOVERY` | `true` | Whether Revora works on its own |
| `AUTONOMOUS_INTERVAL_SECONDS` | `12` | How often it looks for work |

> Revora refuses to start against live Razorpay credentials. A key not beginning
> `rzp_test_` is rejected outright rather than warned about.

---

## Architecture

Full detail in **[ARCHITECTURE.md](./ARCHITECTURE.md)**. The same diagram is inside the
app under **About Revora**, where hovering a stage reveals its stack.

![Revora architecture](./docs/architecture.png)

## Demo notes

- **Demo - Video:** [Revora](https://drive.google.com/file/d/12FN8A-OA9NR0EC_7g4NkigOEUz1tT9jx/view?usp=sharing)

- **No customer is ever contacted.** No email, SMS or voice provider is wired in. Every
  send is recorded as simulated, and the status vocabulary contains no "sent" or
  "delivered" value at all.
- **No real money moves.** Razorpay runs in Test Mode; the default is a deterministic
  local simulation.
- **Contact hours are enforced.** Outside 08:00–19:00 IST messages are blocked — correctly.
  A read-only preview shows what would be said during permitted hours.

## Licence

Built for the Razorpay Buildathon.

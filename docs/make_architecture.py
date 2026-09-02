"""Draw docs/architecture.png.

Kept in the repository so the diagram can be regenerated when the system
changes, rather than becoming a stale export nobody can edit.

Every box below corresponds to a module that exists. Nothing aspirational is
drawn.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

INK = "#111827"
MUTED = "#6b7280"
LINE = "#d1d5db"

UI = "#e0e7ff"
UI_EDGE = "#6366f1"
API = "#dbeafe"
API_EDGE = "#3b82f6"
ENGINE = "#dcfce7"
ENGINE_EDGE = "#16a34a"
GATE = "#fee2e2"
GATE_EDGE = "#dc2626"
LANG = "#ede9fe"
LANG_EDGE = "#8b5cf6"
DATA = "#fef3c7"
DATA_EDGE = "#d97706"

fig, ax = plt.subplots(figsize=(16, 13.4))
ax.set_xlim(0, 100)
ax.set_ylim(0, 86)
ax.axis("off")
fig.patch.set_facecolor("white")


def band(y, height, label, colour):
    ax.add_patch(
        patches.FancyBboxPatch(
            (2, y), 96, height,
            boxstyle="round,pad=0.35,rounding_size=0.8",
            facecolor=colour, edgecolor="none", alpha=0.28, zorder=0,
        )
    )
    # Label sits in its own strip at the top of the band; boxes are placed
    # below it. Overlaying the two made the labels unreadable.
    ax.text(3.4, y + height - 1.1, label, fontsize=10.5, color=MUTED,
            weight="bold", va="top", zorder=1)


def polyline(points, colour=MUTED, lw=1.8, dashed=False, label="", label_at=None):
    """Route a connection through explicit waypoints.

    Long links are drawn in the gaps BETWEEN bands rather than across them. A
    line that passes through three unrelated boxes on its way somewhere is
    worse than no line: a reader cannot tell what it actually connects.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.plot(xs, ys, color=colour, linewidth=lw,
            linestyle="--" if dashed else "-",
            solid_capstyle="round", zorder=4)
    ax.annotate(
        "", xy=points[-1], xytext=points[-2],
        arrowprops=dict(arrowstyle="-|>", color=colour, linewidth=lw,
                        shrinkA=0, shrinkB=2),
        zorder=4,
    )
    if label and label_at:
        ax.text(label_at[0], label_at[1], label, fontsize=7.6, color=MUTED,
                style="italic", ha="center", zorder=6,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.4))


def box(x, y, w, h, title, subtitle="", face=UI, edge=UI_EDGE, bold=True, fs=10):
    ax.add_patch(
        patches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.25,rounding_size=0.6",
            facecolor=face, edgecolor=edge, linewidth=1.6, zorder=2,
        )
    )
    if subtitle:
        ax.text(x + w / 2, y + h * 0.62, title, ha="center", va="center",
                fontsize=fs, weight="bold" if bold else "normal", color=INK, zorder=3)
        ax.text(x + w / 2, y + h * 0.26, subtitle, ha="center", va="center",
                fontsize=fs - 2.2, color=MUTED, zorder=3, style="italic")
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center",
                fontsize=fs, weight="bold" if bold else "normal", color=INK, zorder=3)


def arrow(
    x1, y1, x2, y2, colour=MUTED, style="-|>", lw=1.7, dashed=False,
    label="", elbow=None, label_at=None,
):
    """Draw a connection.

    ``elbow`` takes a matplotlib connectionstyle. Long links are routed as
    right angles rather than diagonals: a diagonal across four bands passes
    through boxes it has nothing to do with, and a reader cannot tell which
    ones it is actually connecting.
    """
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=style, color=colour, linewidth=lw,
            linestyle="--" if dashed else "-",
            shrinkA=3, shrinkB=3,
            connectionstyle=elbow or "arc3,rad=0",
        ),
        zorder=4,
    )
    if label:
        lx, ly = label_at or ((x1 + x2) / 2 + 1.2, (y1 + y2) / 2)
        ax.text(lx, ly, label, fontsize=7.6, color=MUTED, style="italic",
                zorder=6, bbox=dict(facecolor="white", edgecolor="none",
                                    alpha=0.85, pad=1.2))


# ---------------------------------------------------------------- title
ax.text(2, 84.4, "Revora", fontsize=25, weight="bold", color=INK)
ax.text(15.2, 84.9, "autonomous revenue recovery", fontsize=12.5, color=MUTED, style="italic")
ax.text(2, 82.2, "Razorpay Buildathon · Track 03", fontsize=9.5, color=MUTED)

# ---------------------------------------------------------------- interface
band(71.5, 9.4, "INTERFACE   Next.js 14 · React · TypeScript · Tailwind", UI)
for i, (t, sub) in enumerate([
    ("Overview", "recovered · trend"),
    ("Recovery", "cases + detail"),
    ("Communications", "email · SMS · voice"),
    ("Promises", "lifecycle"),
    ("Audit", "every step"),
    ("Run Recovery", "dry-run console"),
]):
    box(4 + i * 15.5, 72.6, 14, 5.4, t, sub, UI, UI_EDGE, fs=9.5)

# ---------------------------------------------------------------- api
band(60.4, 9.4, "API   FastAPI · Pydantic · Uvicorn", API)
for i, (t, sub) in enumerate([
    ("/events", "cases + money"),
    ("/batch", "runs · dry-run"),
    ("/communications", "contacts"),
    ("/promises", "commitments"),
    ("/reports", "recovery · audit"),
    ("/notifications", "derived alerts"),
]):
    box(4 + i * 15.5, 61.5, 14, 5.4, t, sub, API, API_EDGE, fs=9.5)

arrow(50, 72.6, 50, 67.2, UI_EDGE, lw=2.2, label="  polls every 5s")

# ---------------------------------------------------------------- engine
band(40.2, 19.4, "RECOVERY ENGINE   deterministic · authoritative", ENGINE)

box(4, 51.4, 20, 5.4, "1 · Diagnose", "rules decide", ENGINE, ENGINE_EDGE)
box(26.5, 51.4, 20, 5.4, "2 · Decide", "expected value", ENGINE, ENGINE_EDGE)
box(49, 51.4, 20, 5.4, "ML classifier", "advisory · recorded", "#f3f4f6", "#9ca3af", fs=9.5)
box(71.5, 51.4, 24.5, 5.4, "Stopping rules", "attempts · cooldown · caps", ENGINE, ENGINE_EDGE, fs=9.5)

box(4, 42.2, 65, 7.0, "3 · POLICY GATE", "merchant limits · overrides everything above", GATE, GATE_EDGE, fs=13)
box(71.5, 42.2, 24.5, 7.0, "State machine", "terminal is terminal", ENGINE, ENGINE_EDGE, fs=9.5)

arrow(14, 61.5, 14, 56.8, API_EDGE, lw=2.2)
arrow(24, 54.1, 26.5, 54.1, MUTED)
arrow(49, 54.1, 46.5, 54.1, "#9ca3af", dashed=True, lw=1.3)
arrow(36, 51.4, 36, 49.2, MUTED, lw=2.2)

# ---------------------------------------------------------------- language
# Wording only. Nothing in this band decides anything, contacts anyone, or
# records money — which is why no arrow leaves it for the ledger or for
# Promises. It hands finished TEXT to the execution stage below.
band(28.6, 11.0, "LANGUAGE   wording only · no authority", LANG)
box(4, 29.8, 26, 6.4, "Template engine", "YAML + compliance gate", LANG, LANG_EDGE, fs=9.5)
box(33, 29.8, 26, 6.4, "Retrieval (RAG)", "customer-isolated context", LANG, LANG_EDGE, fs=9.5)
box(62, 29.8, 26, 6.4, "Hinglish LLM", "Ollama · Mistral · rewrites wording", LANG, LANG_EDGE, fs=9.5)

arrow(14.5, 42.2, 14.5, 36.6, GATE_EDGE, lw=2.2, label="  approved action only")
arrow(30, 33.0, 33, 33.0, MUTED)
arrow(59, 33.0, 62, 33.0, MUTED, dashed=True, label=" context")

# ---------------------------------------------------------------- execution
# Where anything actually HAPPENS. Promises are born here, from a customer's
# reply — never from the language layer, which only chose the words.
band(15.4, 11.4, "EXECUTION & VERIFICATION   the only stage that acts", "#ecfdf5")
box(4, 16.6, 21, 6.4, "Communication", "simulated · nothing sent", "#ecfdf5", ENGINE_EDGE, fs=9.5)
box(28, 16.6, 21, 6.4, "Customer response", "simulated reply", "#ecfdf5", ENGINE_EDGE, fs=9.5)
box(52, 16.6, 21, 6.4, "Payment action", "retry · await · verify", "#ecfdf5", ENGINE_EDGE, fs=9.5)
box(76, 16.6, 20, 6.4, "Outcome", "recovered · pending · lost", "#ecfdf5", ENGINE_EDGE, fs=9.5)

polyline([(75, 29.8), (75, 27.7), (14.5, 27.7), (14.5, 23.0)],
         colour=LANG_EDGE, lw=1.8,
         label="finished wording", label_at=(45, 28.3))
polyline([(66, 42.2), (66, 37.4), (94, 37.4), (94, 26.2), (62.5, 26.2), (62.5, 23.0)],
         colour=GATE_EDGE, lw=1.8,
         label="permitted action", label_at=(94, 31.0))
arrow(25, 19.8, 28, 19.8, MUTED)
arrow(73, 19.8, 76, 19.8, MUTED)

# ---------------------------------------------------------------- data
band(3.4, 11.0, "DATA   source of truth", DATA)
box(4, 4.6, 21, 6.4, "Recovery ledger", "integer paise", DATA, DATA_EDGE, fs=9.5)
box(28, 4.6, 21, 6.4, "Promises", "commitment lifecycle", DATA, DATA_EDGE, fs=9.5)
box(52, 4.6, 21, 6.4, "Audit trail", "append-only", DATA, DATA_EDGE, fs=9.5)
box(76, 4.6, 20, 6.4, "Redis", "optional · locks only", "#f3f4f6", "#9ca3af", fs=9.5)

# The ledger records EXECUTION outcomes, not anything the language layer made.
polyline([(86, 16.6), (86, 14.9), (14.5, 14.9), (14.5, 11.0)],
         colour=DATA_EDGE, lw=2.0,
         label="verified outcome", label_at=(50, 15.5))
# A promise comes from what the customer SAID, not from the LLM.
arrow(38.5, 16.6, 38.5, 11.0, ENGINE_EDGE, lw=2.0,
      label="stated a date", label_at=(39.6, 13.6))
arrow(62.5, 16.6, 62.5, 11.0, MUTED, dashed=True, lw=1.4,
      label="every step", label_at=(63.6, 13.6))
arrow(90, 61.5, 90, 11.0, DATA_EDGE, dashed=True, lw=1.3)
ax.text(90.6, 34, "reads", fontsize=7.6, color=MUTED, style="italic", rotation=90)

# ---------------------------------------------------------------- orchestration
# LangGraph coordinates the sequence above. It is drawn ALONGSIDE the flow, with
# no arrow into any component, because it neither decides nor generates.
ax.add_patch(
    patches.FancyBboxPatch(
        (0.6, 15.4), 1.6, 33.8,
        boxstyle="round,pad=0.1,rounding_size=0.4",
        facecolor=LANG, edgecolor=LANG_EDGE, linewidth=1.4,
        linestyle="--", alpha=0.55, zorder=1,
    )
)
ax.text(
    1.4, 32.3,
    "LangGraph  ·  workflow / state orchestration only  ·  no decisions, no language",
    rotation=90, ha="center", va="center", fontsize=8.4,
    color=LANG_EDGE, weight="bold", zorder=3,
)

# ---------------------------------------------------------------- autonomy
# The loop drives the WHOLE workflow, so it sits beside the engine that starts
# it — not beside the language layer.
ax.text(
    50, 39.0,
    "Autonomous loop  ·  asyncio  ·  every ~12s  —  nobody presses a button",
    ha="center", fontsize=10, color=GATE_EDGE, weight="bold", style="italic", zorder=5,
)

# ---------------------------------------------------------------- legend
legend = [
    Line2D([0], [0], color=ENGINE_EDGE, lw=3, label="May decide (deterministic)"),
    Line2D([0], [0], color=GATE_EDGE, lw=3, label="Authoritative gate"),
    Line2D([0], [0], color=LANG_EDGE, lw=3, label="No authority (wording / context)"),
    Line2D([0], [0], color="#9ca3af", lw=3, linestyle="--", label="Advisory or optional"),
]
ax.legend(
    handles=legend, loc="lower center", bbox_to_anchor=(0.5, -0.035),
    ncol=4, frameon=False, fontsize=9.5,
)

plt.tight_layout()
fig.savefig("docs/architecture.png", dpi=160, bbox_inches="tight", facecolor="white")
print("  wrote docs/architecture.png")

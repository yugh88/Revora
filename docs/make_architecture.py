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

fig, ax = plt.subplots(figsize=(16, 11.5))
ax.set_xlim(0, 100)
ax.set_ylim(0, 74)
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


def arrow(x1, y1, x2, y2, colour=MUTED, style="-|>", lw=1.7, dashed=False, label=""):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=style, color=colour, linewidth=lw,
            linestyle="--" if dashed else "-",
            shrinkA=3, shrinkB=3, connectionstyle="arc3,rad=0",
        ),
        zorder=4,
    )
    if label:
        ax.text((x1 + x2) / 2 + 1.2, (y1 + y2) / 2, label, fontsize=7.6,
                color=MUTED, style="italic", zorder=5)


# ---------------------------------------------------------------- title
ax.text(2, 72.4, "Revora", fontsize=25, weight="bold", color=INK)
ax.text(15.2, 72.9, "autonomous revenue recovery", fontsize=12.5, color=MUTED, style="italic")
ax.text(2, 70.2, "Razorpay Buildathon · Track 03", fontsize=9.5, color=MUTED)

# ---------------------------------------------------------------- interface
band(59.5, 9.4, "INTERFACE   Next.js 14 · React · TypeScript · Tailwind", UI)
for i, (t, sub) in enumerate([
    ("Overview", "recovered · trend"),
    ("Recovery", "cases + detail"),
    ("Communications", "email · SMS · voice"),
    ("Promises", "lifecycle"),
    ("Audit", "every step"),
    ("Run Recovery", "dry-run console"),
]):
    box(4 + i * 15.5, 60.6, 14, 5.4, t, sub, UI, UI_EDGE, fs=9.5)

# ---------------------------------------------------------------- api
band(48.4, 9.4, "API   FastAPI · Pydantic · Uvicorn", API)
for i, (t, sub) in enumerate([
    ("/events", "cases + money"),
    ("/batch", "runs · dry-run"),
    ("/communications", "contacts"),
    ("/promises", "commitments"),
    ("/reports", "recovery · audit"),
    ("/notifications", "derived alerts"),
]):
    box(4 + i * 15.5, 49.5, 14, 5.4, t, sub, API, API_EDGE, fs=9.5)

arrow(50, 60.6, 50, 55.2, UI_EDGE, lw=2.2, label="  polls every 5s")

# ---------------------------------------------------------------- engine
band(28.2, 19.4, "RECOVERY ENGINE   deterministic · authoritative", ENGINE)

box(4, 39.4, 20, 5.4, "1 · Diagnose", "rules decide", ENGINE, ENGINE_EDGE)
box(26.5, 39.4, 20, 5.4, "2 · Decide", "expected value", ENGINE, ENGINE_EDGE)
box(49, 39.4, 20, 5.4, "ML classifier", "advisory · recorded", "#f3f4f6", "#9ca3af", fs=9.5)
box(71.5, 39.4, 24.5, 5.4, "Stopping rules", "attempts · cooldown · caps", ENGINE, ENGINE_EDGE, fs=9.5)

box(4, 30.2, 65, 7.0, "3 · POLICY GATE", "merchant limits · overrides everything above", GATE, GATE_EDGE, fs=13)
box(71.5, 30.2, 24.5, 7.0, "State machine", "terminal is terminal", ENGINE, ENGINE_EDGE, fs=9.5)

arrow(14, 49.5, 14, 44.8, API_EDGE, lw=2.2)
arrow(24, 42.1, 26.5, 42.1, MUTED)
arrow(49, 42.1, 46.5, 42.1, "#9ca3af", dashed=True, lw=1.3)
arrow(36, 39.4, 36, 37.2, MUTED, lw=2.2)

# ---------------------------------------------------------------- language
band(16.4, 11.0, "LANGUAGE   wording only · no authority", LANG)
box(4, 17.6, 21, 6.4, "Template engine", "YAML + compliance gate", LANG, LANG_EDGE, fs=9.5)
box(28, 17.6, 21, 6.4, "Retrieval (RAG)", "customer-isolated", LANG, LANG_EDGE, fs=9.5)
box(52, 17.6, 21, 6.4, "Hinglish LLM", "Ollama · Mistral", LANG, LANG_EDGE, fs=9.5)
box(76, 17.6, 20, 6.4, "LangGraph", "orchestration", LANG, LANG_EDGE, fs=9.5)

arrow(14.5, 30.2, 14.5, 24.4, GATE_EDGE, lw=2.2, label="  approved actions only")
arrow(25, 20.8, 28, 20.8, MUTED)
arrow(49, 20.8, 52, 20.8, MUTED, dashed=True, label=" context")

# ---------------------------------------------------------------- data
band(4.4, 11.0, "DATA   source of truth", DATA)
box(4, 5.6, 21, 6.4, "Recovery ledger", "integer paise", DATA, DATA_EDGE, fs=9.5)
box(28, 5.6, 21, 6.4, "Audit trail", "append-only", DATA, DATA_EDGE, fs=9.5)
box(52, 5.6, 21, 6.4, "Promises", "commitment lifecycle", DATA, DATA_EDGE, fs=9.5)
box(76, 5.6, 20, 6.4, "Redis", "optional · locks only", "#f3f4f6", "#9ca3af", fs=9.5)

arrow(14.5, 17.6, 14.5, 12.0, LANG_EDGE)
arrow(62.5, 17.6, 62.5, 12.0, LANG_EDGE)
arrow(90, 49.5, 90, 12.0, DATA_EDGE, dashed=True, lw=1.3)
ax.text(90.6, 27, "reads", fontsize=7.6, color=MUTED, style="italic", rotation=90)

# ---------------------------------------------------------------- gateways
box(4, 0.2, 44, 4.0, "Razorpay Test Mode", "sandbox only · never production", "#ecfdf5", ENGINE_EDGE, fs=9.5)
box(52, 0.2, 44, 4.0, "Local simulation", "deterministic · seeded · offline", "#ecfdf5", ENGINE_EDGE, fs=9.5)
arrow(26, 5.6, 26, 4.2, DATA_EDGE)
arrow(74, 5.6, 74, 4.2, DATA_EDGE)

# ---------------------------------------------------------------- autonomy
ax.add_patch(
    patches.FancyBboxPatch(
        (73.5, 42.6), 0.001, 0.001, boxstyle="round", facecolor="none", edgecolor="none"
    )
)
ax.text(
    50, 27.6,
    "Autonomous loop  ·  asyncio  ·  every ~12s  —  nobody presses a button",
    ha="center", fontsize=9.5, color=GATE_EDGE, weight="bold", style="italic", zorder=5,
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

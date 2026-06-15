"""Explainability layer — every recommendation/alert carries a structured
explanation: WHY, EVIDENCE (data it is based on), IMPACT (if you follow it)."""


def fmt_vnd(n) -> str:
    try:
        return f"{int(round(float(n))):,}".replace(",", ".") + "đ"
    except (TypeError, ValueError):
        return str(n)


def explanation(why: str, evidence: list, impact: str) -> dict:
    """Canonical explanation object attached to every recommendation/alert."""
    return {"why": why, "evidence": evidence, "impact": impact}


def render(expl: dict) -> str:
    """Render an explanation object into a Vietnamese, user-facing block."""
    lines = []
    if expl.get("why"):
        lines.append(f"• *Vì sao:* {expl['why']}")
    if expl.get("evidence"):
        ev = "; ".join(str(e) for e in expl["evidence"])
        lines.append(f"• *Dựa trên:* {ev}")
    if expl.get("impact"):
        lines.append(f"• *Nếu làm theo:* {expl['impact']}")
    return "\n".join(lines)

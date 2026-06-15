"""Decisioning layer — decide WHICH alerts to send, at WHAT urgency, and
suppress noise. Uses multi-tier thresholds rather than a single cutoff."""

import logging

import config
import db
import intelligence as intel
from explain import explanation, render, fmt_vnd
from config import CATEGORY_LABELS

logger = logging.getLogger("pfm.decide")

_URGENCY = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}


def _tier_for_usage(usage: float):
    """Highest tier whose threshold is crossed, or None."""
    hit = None
    for tier, thr in config.ALERT_TIERS:
        if usage >= thr:
            hit = tier
    return hit


def transaction_alerts(user_id: str, amount: float, category: str, anomaly: dict | None) -> list:
    """Immediate alerts evaluated right after a new transaction lands."""
    alerts = []
    label = CATEGORY_LABELS.get(category, category)

    if anomaly:
        alerts.append({
            "key": f"anomaly:{category}",
            "tier": "warning",
            "title": f"Giao dịch bất thường ({label})",
            "expl": explanation(
                why=f"Khoản {fmt_vnd(amount)} cao gấp {anomaly['factor']}× mức trung bình 30 ngày của nhóm {label}.",
                evidence=[f"TB 30 ngày {label}: {fmt_vnd(anomaly['avg'])}", f"Giao dịch này: {fmt_vnd(amount)}"],
                impact="Kiểm tra lại nếu không chủ đích; cân nhắc bù trừ ở các nhóm khác để giữ ngân sách.",
            ),
        })

    budget = db.get_budget(user_id, category)
    if budget:
        risks = {r["category"]: r for r in intel.overspend_risk(user_id)}
        r = risks.get(category)
        if r:
            tier = _tier_for_usage(r["usage"])
            if tier:
                alerts.append(_budget_alert(user_id, label, r, tier))
    return alerts


def scheduled_alerts(user_id: str) -> list:
    """Alerts evaluated on a schedule (cron): budget tiers + projected overspend."""
    alerts = []
    for r in intel.overspend_risk(user_id):
        label = CATEGORY_LABELS.get(r["category"], r["category"])
        tier = _tier_for_usage(r["usage"])
        if tier:
            alerts.append(_budget_alert(user_id, label, r, tier))
        elif r["projected_usage"] >= 1.0:
            # Not over yet, but on track to blow the budget this month.
            alerts.append({
                "key": f"forecast:{r['category']}",
                "tier": "warning",
                "title": f"Dự báo vượt ngân sách ({label})",
                "expl": explanation(
                    why=f"Theo nhịp chi hiện tại, nhóm {label} dự kiến đạt {int(r['projected_usage']*100)}% ngân sách cuối tháng.",
                    evidence=[f"Đã chi: {fmt_vnd(r['spent'])}/{fmt_vnd(r['budget'])}",
                              f"Dự báo: {fmt_vnd(r['projected'])}"],
                    impact=f"Giảm ~{fmt_vnd(max(r['projected']-r['budget'],0))} từ giờ tới cuối tháng để không vượt.",
                ),
            })
    return alerts


def _budget_alert(user_id, label, r, tier):
    return {
        "key": f"budget:{r['category']}:{tier}",
        "tier": tier,
        "title": f"Ngân sách {label}: {int(r['usage']*100)}% đã dùng",
        "expl": explanation(
            why=f"Đã dùng {int(r['usage']*100)}% ngân sách nhóm {label} trong tháng (ngưỡng {tier}).",
            evidence=[f"Đã chi: {fmt_vnd(r['spent'])}", f"Ngân sách: {fmt_vnd(r['budget'])}",
                      f"Dự báo cuối tháng: {fmt_vnd(r['projected'])}"],
            impact=("Đã vượt ngân sách — cân nhắc dừng chi nhóm này." if tier == "critical"
                    else f"Còn {fmt_vnd(max(r['budget']-r['spent'],0))} trước khi chạm ngân sách."),
        ),
    }


def render_alert(alert: dict) -> str:
    icon = _URGENCY.get(alert["tier"], "•")
    return f"{icon} *{alert['title']}*\n{render(alert['expl'])}"


def dispatch(user_id: str, chat_id: str, alerts: list, send_text) -> int:
    """Send alerts honoring per-key cooldown. Returns number sent."""
    sent = 0
    for a in alerts:
        if db.alert_recently_sent(user_id, a["key"], config.ALERT_COOLDOWN_HOURS):
            continue
        send_text(chat_id, render_alert(a))
        db.log_alert(user_id, a["key"], a["tier"])
        sent += 1
    return sent

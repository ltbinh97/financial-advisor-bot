"""Periodic report generation — income/expense summary, category breakdown,
budget status, goal progress, forecast, plus an explainable insight."""

import logging
from datetime import datetime, timedelta

import db
import intelligence as intel
import llm
from explain import fmt_vnd
from config import CATEGORY_LABELS

logger = logging.getLogger("pfm.report")


def build_report(user_id: str, period_days: int = 7, month: bool = False) -> str:
    now = datetime.utcnow()
    if month:
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_label = "tháng này"
    else:
        since = now - timedelta(days=period_days)
        period_label = "tuần" if period_days <= 7 else f"{period_days} ngày"
    income = db.total_amount(user_id, since, "income")
    expense = db.total_amount(user_id, since, "expense")
    net = income - expense
    cats = db.category_totals(user_id, since, "expense")[:5]
    risks = intel.overspend_risk(user_id)
    goals = db.list_goals(user_id)
    fc = intel.forecast_month_expense(user_id)

    L = [f"📊 *Báo cáo tài chính {period_label}*", ""]
    L.append(f"• Thu: {fmt_vnd(income)}  |  Chi: {fmt_vnd(expense)}  |  Ròng: {fmt_vnd(net)}")
    st = db.get_settings(user_id)
    if st and st.get("monthly_income"):
        L.append(f"• Thu nhập khai báo: {fmt_vnd(st['monthly_income'])}/tháng")

    if cats:
        L.append("\n*Top chi tiêu:*")
        for c in cats:
            lab = CATEGORY_LABELS.get(c["category"], c["category"])
            L.append(f"  - {lab}: {fmt_vnd(c['total'])} ({c['n']} giao dịch)")

    if risks:
        L.append("\n*Ngân sách tháng này:*")
        for r in risks[:5]:
            lab = CATEGORY_LABELS.get(r["category"], r["category"])
            flag = "🔴" if r["usage"] >= 1 else ("⚠️" if r["usage"] >= 0.9 else "✅")
            L.append(f"  {flag} {lab}: {fmt_vnd(r['spent'])}/{fmt_vnd(r['budget'])} ({int(r['usage']*100)}%)")

    if goals:
        L.append("\n*Mục tiêu tiết kiệm:*")
        for g in goals:
            saved, target = float(g["saved_amount"]), float(g["target_amount"])
            pct = int(saved / target * 100) if target else 0
            L.append(f"  - {g['name']}: {fmt_vnd(saved)}/{fmt_vnd(target)} ({pct}%)")

    L.append(f"\n*Dự báo chi tháng:* {fmt_vnd(fc['projected_month'])} "
             f"(đã chi {fmt_vnd(fc['spent_so_far'])}/{fc['day']} ngày)")

    insight = _insight(income, expense, cats, risks, fc)
    if insight:
        L.append("\n💡 *Nhận định:* " + insight)
    return "\n".join(L)


def _insight(income, expense, cats, risks, fc) -> str:
    """One concise, explainable insight from the LLM grounded in the numbers."""
    facts = {
        "thu": fmt_vnd(income), "chi": fmt_vnd(expense),
        "top_categories": [f"{CATEGORY_LABELS.get(c['category'], c['category'])}={fmt_vnd(c['total'])}" for c in cats],
        "over_budget": [CATEGORY_LABELS.get(r["category"], r["category"]) for r in risks if r["usage"] >= 1],
        "du_bao_chi_thang": fmt_vnd(fc["projected_month"]),
    }
    try:
        return llm.chat(
            "Bạn là cố vấn tài chính. Dựa CHỈ trên số liệu được cung cấp, đưa 1-2 câu nhận định "
            "ngắn gọn kèm 1 hành động cụ thể. Nêu rõ con số làm căn cứ. Tiếng Việt, không bịa số.",
            str(facts), max_tokens=160)
    except Exception:
        logger.exception("insight LLM failed")
        return ""

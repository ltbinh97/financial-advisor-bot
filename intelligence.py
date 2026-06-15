"""Intelligence layer — categorization, anomaly/recurring detection,
cash-flow forecast and overspend-risk estimation."""

import logging
from calendar import monthrange
from datetime import datetime, timedelta

import config
import db
import llm

logger = logging.getLogger("pfm.intel")

# Fast keyword rules; LLM is only used as a fallback for unknown merchants.
_RULES = {
    "an_uong": ["ăn", "an ", "cơm", "com", "cà phê", "ca phe", "coffee", "trà sữa", "quán", "nhà hàng", "food", "grabfood", "lunch", "lẩu", "phở", "bún"],
    "di_chuyen": ["xăng", "xang", "grab", "taxi", "xe máy", "xe buýt", "ô tô", "gửi xe", "bus", "vé xe", "parking", "gojek", "tàu", "máy bay", "vé máy bay", "đi lại"],
    "hoa_don_tien_ich": ["điện", "dien", "nước", "nuoc", "internet", "wifi", "hóa đơn", "hoa don", "evn", "tiền điện", "data", "4g", "cước"],
    "mua_sam": ["shopee", "lazada", "tiki", "siêu thị", "sieu thi", "quần áo", "mua áo", "mua giày", "mua đồ", "shop", "mall"],
    "giai_tri": ["phim", "netflix", "spotify", "game", "karaoke", "du lịch", "bar", "nhậu", "giải trí"],
    "suc_khoe": ["thuốc", "thuoc", "bệnh viện", "benh vien", "khám", "bác sĩ", "nha khoa", "gym", "phòng khám"],
    "giao_duc": ["học", "hoc ", "khóa học", "sách", "sach", "course", "udemy", "học phí"],
    "nha_o": ["thuê nhà", "thue nha", "tiền nhà", "tien nha", "rent", "chung cư", "phòng trọ"],
    "thu_nhap": ["lương", "luong", "thưởng", "thuong", "salary", "thu nhập", "nhận tiền", "hoàn tiền"],
    "tiet_kiem_dau_tu": ["tiết kiệm", "tiet kiem", "đầu tư", "dau tu", "chứng khoán", "gửi tiết kiệm", "vàng"],
}


def categorize(draft: dict) -> str:
    if draft.get("type") == "income":
        return "thu_nhap"
    text = " ".join(str(draft.get(k) or "") for k in ("merchant", "note", "raw")).lower()
    for cat, kws in _RULES.items():
        if any(kw in text for kw in kws):
            return cat
    # LLM fallback
    res = llm.chat_json(
        "Phân loại giao dịch chi tiêu vào DUY NHẤT một mã trong danh sách: "
        + ", ".join(config.CATEGORIES) +
        ". Trả JSON {\"category\": \"<mã>\"}.",
        text or "(không rõ)", max_tokens=60)
    if res and res.get("category") in config.CATEGORIES:
        return res["category"]
    return "khac"


def detect_anomaly(user_id: str, amount: float, category: str) -> dict | None:
    """Flag a transaction that is far above the category's 30-day average."""
    avg = db.category_avg_30d(user_id, category)
    if avg and avg > 0 and amount >= avg * config.ANOMALY_FACTOR:
        return {"avg": avg, "factor": round(amount / avg, 1)}
    return None


def detect_recurring(user_id: str) -> list:
    """Merchants seen in >=2 distinct months -> likely recurring bills."""
    since = datetime.utcnow() - timedelta(days=120)
    txs = db.list_transactions(user_id, since=since, limit=1000)
    by_merchant = {}
    for t in txs:
        if t["type"] != "expense" or not t.get("merchant"):
            continue
        m = t["merchant"].strip().lower()
        month = t["ts"].strftime("%Y-%m")
        by_merchant.setdefault(m, {"months": set(), "amounts": [], "category": t["category"]})
        by_merchant[m]["months"].add(month)
        by_merchant[m]["amounts"].append(float(t["amount"]))
    out = []
    for m, info in by_merchant.items():
        if len(info["months"]) >= 2:
            amts = info["amounts"]
            out.append({
                "merchant": m, "category": info["category"],
                "months": len(info["months"]),
                "avg_amount": round(sum(amts) / len(amts)),
            })
    return sorted(out, key=lambda x: -x["avg_amount"])


def _month_bounds(now=None):
    now = now or datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = monthrange(now.year, now.month)[1]
    return start, now, days_in_month


def forecast_month_expense(user_id: str) -> dict:
    """Project end-of-month total expense from the current run-rate."""
    start, now, days_in_month = _month_bounds()
    spent = db.total_amount(user_id, start, "expense")
    day = max(now.day, 1)
    daily_rate = spent / day
    projected = daily_rate * days_in_month
    return {
        "spent_so_far": round(spent),
        "day": day, "days_in_month": days_in_month,
        "daily_rate": round(daily_rate),
        "projected_month": round(projected),
    }


def overspend_risk(user_id: str) -> list:
    """Per-category budget usage + projection for the current month."""
    start, now, days_in_month = _month_bounds()
    budgets = db.get_budgets(user_id)
    totals = {r["category"]: float(r["total"]) for r in db.category_totals(user_id, start, "expense")}
    day = max(now.day, 1)
    risks = []
    for b in budgets:
        cat = b["category"]
        budget_amt = float(b["amount"])
        spent = totals.get(cat, 0.0)
        projected = (spent / day) * days_in_month
        usage = spent / budget_amt if budget_amt else 0
        proj_usage = projected / budget_amt if budget_amt else 0
        risks.append({
            "category": cat, "budget": round(budget_amt), "spent": round(spent),
            "usage": round(usage, 2), "projected": round(projected),
            "projected_usage": round(proj_usage, 2),
        })
    return sorted(risks, key=lambda x: -x["projected_usage"])

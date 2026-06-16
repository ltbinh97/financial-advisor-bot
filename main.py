"""Personal Finance Management (PFM) agent on GreenNode AgentBase.

Four layers:
  - Ingestion     (ingestion.py)    : manual text, OCR receipts, bank SMS, CSV
  - Intelligence  (intelligence.py) : categorize, anomaly, recurring, forecast, overspend risk
  - Decisioning   (decisioning.py)  : multi-tier alerts, urgency, anti-spam
  - Explainability(explain.py)      : why / evidence / impact on every recommendation

Channel: Zalo Bot (webhook -> POST /invocations). Periodic reports/alerts are
driven by a cron caller POSTing {"cron": "...", "secret": ...} to /invocations.
Platform hard requirements: listen on :8080 and serve GET /health.
"""

import logging
import threading

from greennode_agentbase import GreenNodeAgentBaseApp, RequestContext, PingStatus

import config
import db
import llm
import ingestion
import intelligence as intel
import decisioning
import reports
import channel_zalo as zalo
from explain import fmt_vnd, render, explanation
from config import CATEGORIES, CATEGORY_LABELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("pfm.main")

WELCOME_TEXT = (
    "👋 *Chào mừng bạn đến với Trợ lý Tài chính Cá nhân!*\n\n"
    "Mình giúp bạn ghi chép thu chi, lập ngân sách, đặt mục tiêu tiết kiệm và xem báo cáo — "
    "chỉ bằng cách nhắn tin tự nhiên.\n\n"
    "*Bắt đầu trong 5 bước (thử ngay):*\n"
    "1️⃣ `thu nhập hàng tháng 20tr` — khai báo thu nhập\n"
    "2️⃣ `ngân sách ăn uống 3tr` — đặt hạn mức & bật cảnh báo\n"
    "3️⃣ `ăn trưa 50k` — ghi một khoản chi (hoặc gửi *ảnh hóa đơn*)\n"
    "4️⃣ `mục tiêu mua xe 50tr` — đặt mục tiêu tiết kiệm\n"
    "5️⃣ `báo cáo tháng này` — xem tổng kết & dự báo\n\n"
    "Gõ `help` bất cứ lúc nào để xem đầy đủ tính năng. Cùng bắt đầu nhé! 💪"
)

GREETINGS = {"hi", "hello", "hey", "alo", "chào", "xin chào", "chào bot", "chào bạn",
             "start", "/start", "bắt đầu", "hí", "helu"}

HELP_TEXT = (
    "👋 Mình là *trợ lý tài chính cá nhân*. Bạn có thể:\n\n"
    "• *Ghi giao dịch*: \"ăn trưa 50k\", \"lương 15tr\", hoặc dán SMS ngân hàng\n"
    "• *Gửi ảnh hóa đơn* → mình tự đọc (OCR) và ghi nhận\n"
    "• *Dán CSV* (date,amount,type,merchant) để nhập hàng loạt\n"
    "• *Ngân sách*: \"ngân sách ăn uống 3tr\"\n"
    "• *Ngân sách từ số 0 (ZBB)*: \"phân bổ tự động\" / \"ngân sách zbb\" — giao việc cho mọi đồng thu nhập\n"
    "• *Mục tiêu*: \"mục tiêu mua xe 50tr\"\n"
    "• *Lộ trình tiết kiệm*: \"tôi muốn có 2 tỷ để mua nhà, bao lâu thì đạt?\"\n"
    "• *Thu nhập*: \"thu nhập hàng tháng 20tr\"\n"
    "• *Báo cáo*: \"báo cáo\" — xem tổng kết + dự báo\n"
    "• *Số dư*: \"tôi còn bao nhiêu tiền\" / \"số dư\" — thu trừ chi\n"
    "• *Xem*: \"ngân sách của tôi\", \"mục tiêu của tôi\", \"hóa đơn định kỳ\", \"dự báo\"\n"
    "• *Xóa nhầm*: \"xóa giao dịch gần nhất\" / \"hủy giao dịch\"\n"
    "• Hoặc hỏi bất kỳ điều gì về tài chính cá nhân.\n\n"
    "Mọi cảnh báo/khuyến nghị đều kèm *vì sao – dựa trên dữ liệu nào – ảnh hưởng gì*."
)


# --- Intent routing -------------------------------------------------------

def _norm(t: str) -> str:
    return (t or "").strip().lower()


def _fast_intent(text: str):
    t = _norm(text)
    if t in ("help", "menu", "/help", "/start", "start", "trợ giúp", "tro giup", "hướng dẫn", "huong dan", "bắt đầu"):
        return "help"
    # Undo / delete a transaction — checked before "recent" to avoid the
    # "giao dịch gần" overlap in "xóa giao dịch gần nhất".
    if ("hoàn tác" in t or "undo" in t or
            (any(x in t for x in ("xóa", "xoá", "hủy", "huỷ"))
             and any(y in t for y in ("giao dịch", "giao dich", "khoản", "khoan",
                                       "gần nhất", "gan nhat", "vừa nhập", "vua nhap", "cuối", "cuoi")))):
        return "undo"
    if any(k in t for k in ("báo cáo", "bao cao", "report", "tổng kết", "tong ket")):
        return "report_month" if any(k in t for k in ("tháng", "thang", "month")) else "report"
    if any(k in t for k in ("phân bổ tự động", "tự động phân bổ", "gợi ý ngân sách",
                            "gợi ý phân bổ", "tu dong phan bo", "phan bo tu dong")):
        return "zbb_auto"
    if any(k in t for k in ("ngân sách zbb", "zero-based", "zero based", "ngân sách từ số 0",
                            "ngân sách từ con số 0", "ngan sach tu so 0", "zbb",
                            "phân bổ ngân sách", "chưa phân bổ", "lập ngân sách từ")):
        return "zbb"
    if any(k in t for k in ("ngân sách của tôi", "ngan sach cua toi", "xem ngân sách", "xem ngan sach")):
        return "view_budgets"
    if any(k in t for k in ("mục tiêu của tôi", "muc tieu cua toi", "xem mục tiêu", "xem muc tieu")):
        return "view_goals"
    if any(k in t for k in ("hóa đơn định kỳ", "hoa don dinh ky", "recurring", "định kỳ")):
        return "recurring"
    if any(k in t for k in ("số dư", "so du", "balance", "còn bao nhiêu tiền",
                            "con bao nhieu tien", "nhiêu tiền", "tiền còn lại", "tien con lai",
                            "còn lại bao nhiêu", "con lai bao nhieu", "còn bao nhiêu", "con bao nhieu")):
        return "balance"
    if any(k in t for k in ("dự báo", "du bao", "forecast")):
        return "forecast"
    if any(k in t for k in ("lịch sử", "lich su", "giao dịch gần", "gần đây", "gan day")):
        return "recent"
    return None


_CLASSIFY_SYS = (
    "Bạn phân loại tin nhắn tài chính tiếng Việt thành intent + trích xuất tham số. "
    "Quy ước tiền: k=*1.000, tr/triệu=*1.000.000, 2tr5=2.500.000, tỷ=*1e9. "
    "Mã danh mục hợp lệ: " + ", ".join(CATEGORIES) + ". "
    "Trả JSON: {\"intent\": one of "
    "[set_budget,set_goal,set_income,add_to_goal,transaction,savings_plan,question], "
    "\"amount\": number|null, \"category\": string|null, \"goal_name\": string|null, "
    "\"type\": \"income|expense|null\", \"merchant\": string|null}. "
    "set_budget: đặt hạn mức chi cho 1 danh mục. set_goal: tạo mục tiêu tiết kiệm. "
    "add_to_goal: bỏ tiền vào mục tiêu. question: hỏi tư vấn, không có giao dịch. "
    "QUAN TRỌNG — phân biệt set_income vs transaction:\n"
    "- set_income CHỈ khi có cụm 'hàng tháng'/'mỗi tháng'/'thu nhập hàng tháng' "
    "(khai báo mức thu nhập cố định, KHÔNG phải tiền vừa nhận).\n"
    "- transaction(type=income) khi là một khoản tiền VỪA nhận: lương, thưởng, được trả, "
    "hoàn tiền... (kể cả 'lương 20tr' không kèm 'hàng tháng').\n"
    "Ví dụ: 'lương 20tr' -> {\"intent\":\"transaction\",\"type\":\"income\",\"amount\":20000000}. "
    "'thu nhập hàng tháng 20tr' -> {\"intent\":\"set_income\",\"amount\":20000000}. "
    "'thưởng tết 5tr' -> {\"intent\":\"transaction\",\"type\":\"income\",\"amount\":5000000}. "
    "'ăn trưa 50k' -> {\"intent\":\"transaction\",\"type\":\"expense\",\"amount\":50000}.\n"
    "add_to_goal khi bỏ/góp/tiết kiệm tiền CHO một mục tiêu đã có. "
    "'tiết kiệm cho mua xe 5tr' -> {\"intent\":\"add_to_goal\",\"goal_name\":\"mua xe\",\"amount\":5000000}. "
    "'bỏ ống 2tr cho quỹ du lịch' -> {\"intent\":\"add_to_goal\",\"goal_name\":\"du lịch\",\"amount\":2000000}. "
    "'mục tiêu mua nhà 1 tỷ' -> {\"intent\":\"set_goal\",\"goal_name\":\"mua nhà\",\"amount\":1000000000}.\n"
    "savings_plan khi hỏi CẦN BAO LÂU / NÊN TIẾT KIỆM THẾ NÀO để đạt một số tiền (xin lộ trình, "
    "chưa yêu cầu tạo mục tiêu). amount = số tiền mục tiêu. "
    "'tôi muốn có 2 tỷ để mua nhà, nên tiết kiệm thế nào' -> "
    "{\"intent\":\"savings_plan\",\"amount\":2000000000,\"goal_name\":\"mua nhà\"}. "
    "'bao lâu để có 500 triệu' -> {\"intent\":\"savings_plan\",\"amount\":500000000}."
)


def classify(text: str) -> dict:
    res = llm.chat_json(_CLASSIFY_SYS, text, max_tokens=200)
    return res if isinstance(res, dict) else {"intent": "question"}


# --- Handlers -------------------------------------------------------------

def _record_and_react(chat_id, user_id, draft):
    # Duplicate guard for automated ingestion (OCR receipts, bank SMS): re-sending
    # the same receipt/notification should not create a second transaction.
    if draft.get("source") in ("ocr", "bank_sms"):
        dup = db.find_duplicate(user_id, draft["amount"], draft["type"],
                                draft.get("merchant"), draft.get("ts"))
        if dup:
            when = dup["ts"].strftime("%d/%m") if dup.get("ts") else "trước đó"
            zalo.send_text(
                chat_id,
                f"⚠️ Giao dịch này trùng với khoản đã ghi ({fmt_vnd(draft['amount'])}"
                + (f" · {draft['merchant']}" if draft.get("merchant") else "")
                + f", ngày {when}). Đã bỏ qua để tránh trùng lặp.\n"
                + "_Nếu là khoản khác, hãy nhập tay kèm ghi chú để phân biệt._")
            return

    category = intel.categorize(draft)
    anomaly = intel.detect_anomaly(user_id, draft["amount"], category) if draft["type"] == "expense" else None
    tx = db.add_transaction(
        user_id, draft["amount"], draft["type"], merchant=draft.get("merchant"),
        category=category, note=draft.get("note"), source=draft.get("source", "manual"),
        raw=draft.get("raw"), ts=draft.get("ts"))
    lab = CATEGORY_LABELS.get(category, category)
    sign = "＋" if draft["type"] == "income" else "－"
    msg = (f"✅ Đã ghi: {sign}{fmt_vnd(draft['amount'])} · {lab}"
           + (f" · {draft['merchant']}" if draft.get("merchant") else "")
           + f"\n_(nguồn: {draft.get('source','manual')})_")
    zalo.send_text(chat_id, msg)
    # Decisioning: immediate alerts (anomaly + budget tier), with explanation.
    alerts = decisioning.transaction_alerts(user_id, draft["amount"], category, anomaly)
    decisioning.dispatch(user_id, chat_id, alerts, zalo.send_text)


def _handle_set_budget(chat_id, user_id, fields):
    amount, cat = fields.get("amount"), fields.get("category")
    if not amount or cat not in CATEGORIES:
        zalo.send_text(chat_id, "Bạn cho mình rõ *danh mục* và *số tiền*, vd: \"ngân sách ăn uống 3tr\".")
        return
    db.set_budget(user_id, cat, float(amount))
    lab = CATEGORY_LABELS.get(cat, cat)
    msg = (f"✅ Đặt ngân sách *{lab}*: {fmt_vnd(amount)}/tháng.\n"
           f"Mình sẽ cảnh báo theo 3 mốc 70% / 90% / 100%.")
    # Tie into ZBB: show how much income is still unallocated.
    income, allocated, unalloc, _ = _zbb_numbers(user_id)
    if income > 0:
        if abs(unalloc) < 1:
            msg += "\n✅ Đã phân bổ hết thu nhập (ZBB cân bằng)."
        elif unalloc > 0:
            msg += f"\n⚖️ Chưa phân bổ: {fmt_vnd(unalloc)}."
        else:
            msg += f"\n🔴 Vượt thu nhập {fmt_vnd(-unalloc)} — cân nhắc giảm bớt."
    zalo.send_text(chat_id, msg)


# Zero-based starting allocation (fractions of income). Sums to 1.0.
ZBB_TEMPLATE = [
    ("nha_o", 0.25), ("an_uong", 0.15), ("di_chuyen", 0.10),
    ("hoa_don_tien_ich", 0.10), ("mua_sam", 0.05), ("giai_tri", 0.05),
    ("suc_khoe", 0.05), ("giao_duc", 0.05), ("tiet_kiem_dau_tu", 0.20),
]


def _zbb_numbers(user_id):
    """Return (income, allocated, unallocated, budgets) for the month."""
    st = db.get_settings(user_id)
    income = float(st["monthly_income"]) if st and st.get("monthly_income") else 0.0
    budgets = db.get_budgets(user_id)
    allocated = sum(float(b["amount"]) for b in budgets)
    return income, allocated, income - allocated, budgets


def _handle_zbb_status(chat_id, user_id):
    income, allocated, unalloc, budgets = _zbb_numbers(user_id)
    if income <= 0:
        zalo.send_text(chat_id, "Lập ngân sách từ số 0 cần biết *thu nhập* trước.\n"
                                "Gõ: \"thu nhập hàng tháng 20tr\", rồi \"phân bổ tự động\".")
        return
    L = ["💸 *Ngân sách Zero-based (tháng)*", f"Thu nhập: {fmt_vnd(income)}", ""]
    if budgets:
        L.append("*Đã phân bổ (mỗi đồng một nhiệm vụ):*")
        for b in sorted(budgets, key=lambda x: -float(x["amount"])):
            L.append(f"  • {CATEGORY_LABELS.get(b['category'], b['category'])}: {fmt_vnd(b['amount'])}")
        L.append(f"\nTổng phân bổ: {fmt_vnd(allocated)}")
    else:
        L.append("_Chưa phân bổ nhóm nào._")
    if abs(unalloc) < 1:
        L.append("\n✅ *Chưa phân bổ: 0đ* — hoàn hảo! Ngân sách ZBB đã cân bằng.")
    elif unalloc > 0:
        L.append(f"\n⚖️ *Chưa phân bổ: {fmt_vnd(unalloc)}* — hãy giao việc cho số này để về 0.")
        L.append("Gõ \"phân bổ tự động\" để mình gợi ý, hoặc \"ngân sách <nhóm> <số tiền>\".")
    else:
        L.append(f"\n🔴 *Vượt phân bổ {fmt_vnd(-unalloc)}* — tổng ngân sách lớn hơn thu nhập, hãy giảm bớt.")
    L.append("\n" + render(explanation(
        why="ZBB: thu nhập − tổng phân bổ phải = 0 (mỗi đồng đều có nhiệm vụ).",
        evidence=[f"Thu nhập {fmt_vnd(income)}", f"Đã phân bổ {fmt_vnd(allocated)}"],
        impact="Đưa 'chưa phân bổ' về 0 giúp bạn chủ động với từng đồng thu nhập.")))
    zalo.send_text(chat_id, "\n".join(L))


def _handle_zbb_auto(chat_id, user_id):
    st = db.get_settings(user_id)
    income = float(st["monthly_income"]) if st and st.get("monthly_income") else 0.0
    if income <= 0:
        zalo.send_text(chat_id, "Mình cần biết *thu nhập hàng tháng* để phân bổ từ số 0.\n"
                                "Gõ: \"thu nhập hàng tháng 20tr\".")
        return
    alloc, running = {}, 0
    for cat, frac in ZBB_TEMPLATE:
        amt = round(income * frac / 1000) * 1000
        alloc[cat] = amt
        running += amt
    # Push the rounding remainder into savings so the plan sums EXACTLY to income.
    alloc["tiet_kiem_dau_tu"] += int(income - running)
    for cat, amt in alloc.items():
        db.set_budget(user_id, cat, amt)
    zalo.send_text(chat_id, "✨ Đã lập ngân sách *Zero-based* khớp 100% thu nhập "
                            f"({fmt_vnd(income)}). Mỗi đồng đã có nhiệm vụ.\n"
                            "Bạn có thể chỉnh từng nhóm: \"ngân sách ăn uống 3tr\".")
    _handle_zbb_status(chat_id, user_id)


def _handle_set_goal(chat_id, user_id, fields):
    amount, name = fields.get("amount"), fields.get("goal_name") or "Mục tiêu"
    if not amount:
        zalo.send_text(chat_id, "Bạn nêu *tên* và *số tiền* mục tiêu, vd: \"mục tiêu mua xe 50tr\".")
        return
    g = db.add_goal(user_id, name, float(amount))
    zalo.send_text(chat_id, f"🎯 Đã tạo mục tiêu *{g['name']}*: {fmt_vnd(amount)}.\n"
                            f"Bỏ tiền vào bằng: \"tiết kiệm cho {name} 1tr\".")


def _fmt_int(n):
    return f"{int(round(n)):,}".replace(",", ".")


def _handle_savings_plan(chat_id, user_id, target, goal_name=None):
    if not target or float(target) <= 0:
        zalo.send_text(chat_id, "Bạn cho mình biết số tiền mục tiêu nhé, vd: "
                                "\"tôi muốn có 2 tỷ để mua nhà, nên tiết kiệm thế nào?\".")
        return
    target = float(target)
    cf = intel.estimate_monthly_cashflow(user_id)
    income, expense, savings = cf["income"], cf["expense"], cf["savings"]

    if income <= 0:
        zalo.send_text(chat_id, "Mình chưa biết thu nhập của bạn nên chưa tính được lộ trình.\n"
                                "Hãy khai báo: \"thu nhập hàng tháng 20tr\" và ghi vài khoản chi, "
                                "rồi hỏi lại nhé.")
        return

    # Subtract any progress already saved toward a matching goal.
    saved = 0.0
    if goal_name:
        for g in db.list_goals(user_id):
            if goal_name.lower() in (g["name"] or "").lower():
                saved = float(g["saved_amount"]); break
    remaining = max(target - saved, 0)

    L = [f"🎯 *Kế hoạch đạt {fmt_vnd(target)}*"
         + (f" ({goal_name})" if goal_name else ""), ""]
    L.append(f"• Thu ~{fmt_vnd(income)}/tháng · Chi ~{fmt_vnd(expense)}/tháng · "
             f"Để dành ~{fmt_vnd(savings)}/tháng")
    if saved > 0:
        L.append(f"• Đã tích lũy {fmt_vnd(saved)} → còn cần {fmt_vnd(remaining)}")

    if savings <= 0:
        L.append("\n⚠️ Hiện *chi ≥ thu* nên chưa tích lũy được. Cần tăng thu nhập hoặc giảm chi.")
        L.append("Nếu muốn đạt mục tiêu, cần để dành mỗi tháng:")
        for yrs in (5, 10, 15):
            L.append(f"  • Trong {yrs} năm → {fmt_vnd(remaining / (yrs * 12))}/tháng")
    else:
        months = remaining / savings
        yrs, mo = int(months // 12), int(round(months % 12))
        days = int(round(months * 30.44))
        L.append(f"\n⏳ Với nhịp hiện tại: ~*{yrs} năm {mo} tháng* "
                 f"(~{_fmt_int(months)} tháng · ~{_fmt_int(days)} ngày)")
        L.append("\nMuốn nhanh hơn — mức để dành cần thiết:")
        for yrs2 in (3, 5, 10):
            L.append(f"  • Đạt trong {yrs2} năm → {fmt_vnd(remaining / (yrs2 * 12))}/tháng")

    L.append("\n" + render(explanation(
        why="Thời gian = số tiền còn cần ÷ mức để dành mỗi tháng (thu − chi).",
        evidence=[f"Thu ~{fmt_vnd(income)}", f"Chi ~{fmt_vnd(expense)}", f"Để dành ~{fmt_vnd(savings)}/tháng"],
        impact="Mỗi đồng giảm chi/tăng thu đều rút ngắn thời gian đạt mục tiêu.")))
    L.append("\n_Gõ \"mục tiêu " + (goal_name or "của tôi") + f" {_fmt_int(target)}\" để mình theo dõi tiến độ._")
    zalo.send_text(chat_id, "\n".join(L))


def _handle_set_income(chat_id, user_id, fields):
    amount = fields.get("amount")
    if not amount:
        zalo.send_text(chat_id, "Bạn cho biết *thu nhập hàng tháng*, vd: \"thu nhập hàng tháng 20tr\".")
        return
    db.set_monthly_income(user_id, float(amount))
    zalo.send_text(chat_id, f"✅ Đã lưu thu nhập hàng tháng: {fmt_vnd(amount)}.")


def _handle_add_to_goal(chat_id, user_id, fields):
    amount, name = fields.get("amount"), _norm(fields.get("goal_name") or "")
    goals = db.list_goals(user_id)
    if not amount or not goals:
        zalo.send_text(chat_id, "Bạn chưa có mục tiêu nào, hoặc thiếu số tiền. Tạo: \"mục tiêu ... <số tiền>\".")
        return
    target = next((g for g in goals if name and name in g["name"].lower()), goals[0])
    g = db.add_to_goal(user_id, target["id"], float(amount))
    saved, tgt = float(g["saved_amount"]), float(g["target_amount"])
    pct = int(saved / tgt * 100) if tgt else 0
    zalo.send_text(chat_id, f"🎯 *{g['name']}*: +{fmt_vnd(amount)} → {fmt_vnd(saved)}/{fmt_vnd(tgt)} ({pct}%).")


def _handle_view_budgets(chat_id, user_id):
    risks = intel.overspend_risk(user_id)
    if not risks:
        zalo.send_text(chat_id, "Bạn chưa đặt ngân sách. Vd: \"ngân sách ăn uống 3tr\".")
        return
    L = ["*Ngân sách tháng này:*"]
    for r in risks:
        lab = CATEGORY_LABELS.get(r["category"], r["category"])
        flag = "🔴" if r["usage"] >= 1 else ("⚠️" if r["usage"] >= 0.9 else "✅")
        L.append(f"{flag} {lab}: {fmt_vnd(r['spent'])}/{fmt_vnd(r['budget'])} ({int(r['usage']*100)}%) · dự báo {fmt_vnd(r['projected'])}")
    zalo.send_text(chat_id, "\n".join(L))


def _handle_view_goals(chat_id, user_id):
    goals = db.list_goals(user_id)
    if not goals:
        zalo.send_text(chat_id, "Bạn chưa có mục tiêu. Vd: \"mục tiêu mua xe 50tr\".")
        return
    L = ["*Mục tiêu tiết kiệm:*"]
    for g in goals:
        saved, tgt = float(g["saved_amount"]), float(g["target_amount"])
        pct = int(saved / tgt * 100) if tgt else 0
        L.append(f"🎯 {g['name']}: {fmt_vnd(saved)}/{fmt_vnd(tgt)} ({pct}%)")
    zalo.send_text(chat_id, "\n".join(L))


def _handle_recurring(chat_id, user_id):
    rec = intel.detect_recurring(user_id)
    if not rec:
        zalo.send_text(chat_id, "Chưa phát hiện hóa đơn định kỳ (cần ≥2 tháng dữ liệu cùng nơi chi).")
        return
    L = ["*Hóa đơn/chi tiêu định kỳ phát hiện được:*"]
    for r in rec[:8]:
        lab = CATEGORY_LABELS.get(r["category"], r["category"])
        L.append(f"🔁 {r['merchant']} ({lab}): ~{fmt_vnd(r['avg_amount'])}/tháng · {r['months']} tháng")
    zalo.send_text(chat_id, "\n".join(L))


def _handle_balance(chat_id, user_id):
    b = db.net_balance(user_id)
    if b["income"] == 0 and b["expense"] == 0:
        zalo.send_text(chat_id, "Bạn chưa ghi giao dịch nào nên số dư đang là 0đ.\n"
                                "Thử ghi: \"lương 20tr\" hoặc \"ăn trưa 50k\".")
        return
    bal = b["balance"]
    emoji = "💰" if bal >= 0 else "🔴"
    expl = explanation(
        why="Số dư = tổng thu đã ghi − tổng chi đã ghi.",
        evidence=[f"Tổng thu: {fmt_vnd(b['income'])}", f"Tổng chi: {fmt_vnd(b['expense'])}"],
        impact=("Bạn đang chi nhiều hơn thu — cân nhắc cắt giảm chi tiêu."
                if bal < 0 else "Khoản dư này có thể đưa vào tiết kiệm hoặc mục tiêu."))
    zalo.send_text(chat_id, f"{emoji} *Số dư hiện tại: {fmt_vnd(bal)}*\n{render(expl)}")


def _handle_forecast(chat_id, user_id):
    fc = intel.forecast_month_expense(user_id)
    expl = explanation(
        why=f"Dự báo dựa trên nhịp chi trung bình {fmt_vnd(fc['daily_rate'])}/ngày.",
        evidence=[f"Đã chi {fmt_vnd(fc['spent_so_far'])} trong {fc['day']}/{fc['days_in_month']} ngày"],
        impact=f"Nếu giữ nhịp này, tổng chi tháng ~{fmt_vnd(fc['projected_month'])}.")
    zalo.send_text(chat_id, f"🔮 *Dự báo chi tháng:* {fmt_vnd(fc['projected_month'])}\n{render(expl)}")


def _handle_undo(chat_id, user_id):
    tx = db.get_last_transaction(user_id)
    if not tx:
        zalo.send_text(chat_id, "Không có giao dịch nào để xóa.")
        return
    db.delete_transaction(user_id, tx["id"])
    lab = CATEGORY_LABELS.get(tx["category"], tx["category"])
    sign = "＋" if tx["type"] == "income" else "－"
    when = tx["ts"].strftime("%d/%m") if tx.get("ts") else ""
    zalo.send_text(
        chat_id,
        f"🗑️ Đã xóa giao dịch gần nhất: {sign}{fmt_vnd(tx['amount'])} · {lab}"
        + (f" · {tx['merchant']}" if tx.get("merchant") else "")
        + (f" ({when})" if when else "")
        + "\n_Số dư/ngân sách đã được cập nhật lại._")


def _handle_recent(chat_id, user_id):
    txs = db.list_transactions(user_id, limit=10)
    if not txs:
        zalo.send_text(chat_id, "Chưa có giao dịch nào. Thử: \"ăn trưa 50k\".")
        return
    L = ["*10 giao dịch gần nhất:*"]
    for t in txs:
        lab = CATEGORY_LABELS.get(t["category"], t["category"])
        sign = "＋" if t["type"] == "income" else "－"
        L.append(f"{t['ts'].strftime('%d/%m')} {sign}{fmt_vnd(t['amount'])} · {lab}"
                 + (f" · {t['merchant']}" if t.get("merchant") else ""))
    zalo.send_text(chat_id, "\n".join(L))


def _handle_question(chat_id, text):
    reply = llm.chat(
        "Bạn là cố vấn tài chính cá nhân tiếng Việt, ngắn gọn (≤1500 ký tự), thực tế, "
        "có gạch đầu dòng; không hứa lợi nhuận, luôn nhắc rủi ro với quyết định lớn.",
        text, max_tokens=600)
    zalo.send_text(chat_id, reply or "Bạn mô tả rõ hơn về vấn đề tài chính nhé.")


# --- Message processing ---------------------------------------------------

def _welcome_if_new(chat_id, user_id) -> bool:
    """Send the welcome message the first time a user contacts the bot."""
    if db.mark_welcomed_if_new(user_id):
        zalo.send_text(chat_id, WELCOME_TEXT)
        return True
    return False


def process_text(chat_id, text):
    user_id = chat_id
    try:
        welcomed = _welcome_if_new(chat_id, user_id)
        # If their very first message is just a greeting/help, the welcome covers it.
        if welcomed and (_norm(text) in GREETINGS or _fast_intent(text) == "help"):
            return
        fast = _fast_intent(text)
        if fast == "help":
            return zalo.send_text(chat_id, HELP_TEXT)
        if fast == "report":
            return zalo.send_text(chat_id, reports.build_report(user_id, 7))
        if fast == "report_month":
            return zalo.send_text(chat_id, reports.build_report(user_id, month=True))
        if fast == "zbb":
            return _handle_zbb_status(chat_id, user_id)
        if fast == "zbb_auto":
            return _handle_zbb_auto(chat_id, user_id)
        if fast == "view_budgets":
            return _handle_view_budgets(chat_id, user_id)
        if fast == "view_goals":
            return _handle_view_goals(chat_id, user_id)
        if fast == "recurring":
            return _handle_recurring(chat_id, user_id)
        if fast == "balance":
            return _handle_balance(chat_id, user_id)
        if fast == "forecast":
            return _handle_forecast(chat_id, user_id)
        if fast == "recent":
            return _handle_recent(chat_id, user_id)
        if fast == "undo":
            return _handle_undo(chat_id, user_id)

        # Bank SMS fast path before generic classification.
        if ingestion.looks_like_bank_sms(text):
            draft = ingestion.parse_bank_sms(text)
            if draft:
                return _record_and_react(chat_id, user_id, draft)

        # CSV (multi-line with header)
        if "\n" in text and "," in text.splitlines()[0] and "amount" in text.splitlines()[0].lower():
            drafts = ingestion.parse_csv(text)
            for d in drafts:
                _record_and_react(chat_id, user_id, d)
            return zalo.send_text(chat_id, f"✅ Đã nhập {len(drafts)} giao dịch từ CSV.")

        fields = classify(text)
        intent = fields.get("intent", "question")
        if intent == "set_budget":
            return _handle_set_budget(chat_id, user_id, fields)
        if intent == "set_goal":
            return _handle_set_goal(chat_id, user_id, fields)
        if intent == "set_income":
            return _handle_set_income(chat_id, user_id, fields)
        if intent == "add_to_goal":
            return _handle_add_to_goal(chat_id, user_id, fields)
        if intent == "savings_plan":
            return _handle_savings_plan(chat_id, user_id, fields.get("amount"), fields.get("goal_name"))
        if intent == "transaction" and fields.get("amount"):
            draft = {"amount": float(fields["amount"]),
                     "type": fields.get("type") if fields.get("type") in ("income", "expense") else "expense",
                     "merchant": fields.get("merchant"), "note": None,
                     "ts": None, "source": "manual", "raw": text}
            return _record_and_react(chat_id, user_id, draft)
        return _handle_question(chat_id, text)
    except Exception:
        logger.exception("process_text failed")
        zalo.send_text(chat_id, "Xin lỗi, có lỗi khi xử lý. Bạn thử lại nhé.")


def _extract_image_url(message: dict):
    """Find an image/attachment URL in a Zalo message payload (shape varies)."""
    # Direct string fields.
    for k in ("url", "photo", "image_url", "photo_url", "thumb", "href", "link"):
        v = message.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    # Nested object fields.
    for k in ("image", "photo", "attachment", "file", "media"):
        v = message.get(k)
        if isinstance(v, dict):
            for kk in ("url", "href", "link", "payload", "thumb"):
                u = v.get(kk)
                if isinstance(u, str) and u.startswith("http"):
                    return u
    # List of attachments.
    for k in ("attachments", "photos", "images", "media"):
        v = message.get(k)
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for kk in ("url", "href", "link", "payload"):
                    u = first.get(kk)
                    if isinstance(u, str) and u.startswith("http"):
                        return u
    return None


def process_image(chat_id, image_url):
    user_id = chat_id
    try:
        _welcome_if_new(chat_id, user_id)
        logger.info("OCR start url=%s", (image_url or "")[:120])
        draft = ingestion.parse_receipt_image(image_url)
        logger.info("OCR result=%s", draft)
        if not draft:
            return zalo.send_text(chat_id, "Mình chưa đọc được hóa đơn. Bạn chụp rõ tổng tiền hơn nhé, "
                                           "hoặc nhập tay: \"siêu thị 250k\".")
        _record_and_react(chat_id, user_id, draft)
    except Exception:
        logger.exception("process_image failed")
        zalo.send_text(chat_id, "Xin lỗi, lỗi khi đọc ảnh hóa đơn.")


# --- Cron sweep -----------------------------------------------------------

def run_cron(kind: str):
    users = db.list_active_users()
    logger.info("cron %s for %d users", kind, len(users))
    for uid in users:
        chat_id = uid  # Zalo PRIVATE: chat_id == user_id
        try:
            if kind in ("daily", "alerts"):
                alerts = decisioning.scheduled_alerts(uid)
                decisioning.dispatch(uid, chat_id, alerts, zalo.send_text)
            if kind in ("weekly", "report"):
                zalo.send_text(chat_id, reports.build_report(uid, 7))
        except Exception:
            logger.exception("cron failed for user %s", uid)


# --- App ------------------------------------------------------------------

app = GreenNodeAgentBaseApp()


@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    # Cron trigger (scheduler POSTs to /invocations).
    if payload.get("cron"):
        if config.CRON_SECRET and payload.get("secret") != config.CRON_SECRET:
            return {"ok": False, "error": "unauthorized"}
        kind = payload["cron"]
        threading.Thread(target=run_cron, args=(kind,), daemon=True).start()
        return {"ok": True, "cron": kind}

    event = payload.get("event_name") or ""
    message = payload.get("message") or {}
    sender = message.get("from") or {}
    chat_id = (message.get("chat") or {}).get("id")

    if sender.get("is_bot") or not chat_id:
        return {"ok": True, "skipped": "ignored"}

    if event == "message.text.received":
        text = (message.get("text") or "").strip()
        if text:
            threading.Thread(target=process_text, args=(chat_id, text), daemon=True).start()
        return {"ok": True}

    # Diagnostic: log the shape of non-text events so we learn Zalo's payload format.
    logger.info("non-text event=%s message_keys=%s", event, list(message.keys()))

    # Image / receipt / attachment events -> OCR (stickers excluded).
    if "sticker" not in event:
        image_url = _extract_image_url(message)
        if image_url:
            zalo.typing(chat_id)
            threading.Thread(target=process_image, args=(chat_id, image_url), daemon=True).start()
            return {"ok": True, "ocr": True}

    return {"ok": True, "skipped": event or "no_event"}


@app.ping
def health_check() -> PingStatus:
    return PingStatus.HEALTHY


# Initialize DB at import time (runtime starts the module, then serves).
try:
    db.init_db()
except Exception:
    logger.exception("DB init failed at startup")


if __name__ == "__main__":
    app.run(port=8080, host="0.0.0.0")

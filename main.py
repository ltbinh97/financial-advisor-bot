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

HELP_TEXT = (
    "👋 Mình là *trợ lý tài chính cá nhân*. Bạn có thể:\n\n"
    "• *Ghi giao dịch*: \"ăn trưa 50k\", \"lương 15tr\", hoặc dán SMS ngân hàng\n"
    "• *Gửi ảnh hóa đơn* → mình tự đọc (OCR) và ghi nhận\n"
    "• *Dán CSV* (date,amount,type,merchant) để nhập hàng loạt\n"
    "• *Ngân sách*: \"ngân sách ăn uống 3tr\"\n"
    "• *Mục tiêu*: \"mục tiêu mua xe 50tr\"\n"
    "• *Thu nhập*: \"thu nhập hàng tháng 20tr\"\n"
    "• *Báo cáo*: \"báo cáo\" — xem tổng kết + dự báo\n"
    "• *Xem*: \"ngân sách của tôi\", \"mục tiêu của tôi\", \"hóa đơn định kỳ\", \"dự báo\"\n"
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
    if any(k in t for k in ("báo cáo", "bao cao", "report", "tổng kết", "tong ket")):
        return "report"
    if any(k in t for k in ("ngân sách của tôi", "ngan sach cua toi", "xem ngân sách", "xem ngan sach")):
        return "view_budgets"
    if any(k in t for k in ("mục tiêu của tôi", "muc tieu cua toi", "xem mục tiêu", "xem muc tieu")):
        return "view_goals"
    if any(k in t for k in ("hóa đơn định kỳ", "hoa don dinh ky", "recurring", "định kỳ")):
        return "recurring"
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
    "[set_budget,set_goal,set_income,add_to_goal,transaction,question], "
    "\"amount\": number|null, \"category\": string|null, \"goal_name\": string|null, "
    "\"type\": \"income|expense|null\", \"merchant\": string|null}. "
    "set_budget: đặt hạn mức chi cho 1 danh mục. set_goal: tạo mục tiêu tiết kiệm. "
    "set_income: khai báo thu nhập hàng tháng. add_to_goal: bỏ tiền vào mục tiêu. "
    "transaction: một khoản thu/chi đã xảy ra. question: hỏi tư vấn, không có giao dịch."
)


def classify(text: str) -> dict:
    res = llm.chat_json(_CLASSIFY_SYS, text, max_tokens=200)
    return res if isinstance(res, dict) else {"intent": "question"}


# --- Handlers -------------------------------------------------------------

def _record_and_react(chat_id, user_id, draft):
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
    zalo.send_text(chat_id, f"✅ Đặt ngân sách *{lab}*: {fmt_vnd(amount)}/tháng.\n"
                            f"Mình sẽ cảnh báo theo 3 mốc 70% / 90% / 100%.")


def _handle_set_goal(chat_id, user_id, fields):
    amount, name = fields.get("amount"), fields.get("goal_name") or "Mục tiêu"
    if not amount:
        zalo.send_text(chat_id, "Bạn nêu *tên* và *số tiền* mục tiêu, vd: \"mục tiêu mua xe 50tr\".")
        return
    g = db.add_goal(user_id, name, float(amount))
    zalo.send_text(chat_id, f"🎯 Đã tạo mục tiêu *{g['name']}*: {fmt_vnd(amount)}.\n"
                            f"Bỏ tiền vào bằng: \"tiết kiệm cho {name} 1tr\".")


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


def _handle_forecast(chat_id, user_id):
    fc = intel.forecast_month_expense(user_id)
    expl = explanation(
        why=f"Dự báo dựa trên nhịp chi trung bình {fmt_vnd(fc['daily_rate'])}/ngày.",
        evidence=[f"Đã chi {fmt_vnd(fc['spent_so_far'])} trong {fc['day']}/{fc['days_in_month']} ngày"],
        impact=f"Nếu giữ nhịp này, tổng chi tháng ~{fmt_vnd(fc['projected_month'])}.")
    zalo.send_text(chat_id, f"🔮 *Dự báo chi tháng:* {fmt_vnd(fc['projected_month'])}\n{render(expl)}")


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

def process_text(chat_id, text):
    user_id = chat_id
    try:
        fast = _fast_intent(text)
        if fast == "help":
            return zalo.send_text(chat_id, HELP_TEXT)
        if fast == "report":
            return zalo.send_text(chat_id, reports.build_report(user_id, 7))
        if fast == "view_budgets":
            return _handle_view_budgets(chat_id, user_id)
        if fast == "view_goals":
            return _handle_view_goals(chat_id, user_id)
        if fast == "recurring":
            return _handle_recurring(chat_id, user_id)
        if fast == "forecast":
            return _handle_forecast(chat_id, user_id)
        if fast == "recent":
            return _handle_recent(chat_id, user_id)

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


def process_image(chat_id, image_url):
    user_id = chat_id
    try:
        draft = ingestion.parse_receipt_image(image_url)
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

    event = payload.get("event_name")
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

    # Image/receipt events (event name varies: image/photo).
    if event and ("image" in event or "photo" in event):
        image_url = message.get("url") or message.get("photo") or (message.get("image") or {}).get("url")
        if image_url:
            zalo.typing(chat_id)
            threading.Thread(target=process_image, args=(chat_id, image_url), daemon=True).start()
        return {"ok": True}

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

"""Comprehensive scenario test harness for the PFM bot.

Runs against the real router (main.process_text / main.handler) with Zalo sends
captured (not sent) and a throwaway Postgres. LLM calls hit the real GreenNode AIP.

Usage (inside container with deps + DATABASE_URL pointing at a test DB):
    python scenario_tests.py
"""

import itertools
from datetime import datetime, timedelta

import db
import channel_zalo

# --- Capture outgoing Zalo messages instead of sending ---
SENT = []
def _fake_call(method, body):
    SENT.append((method, body))
    return {"ok": True}
channel_zalo._call = _fake_call

import main
import intelligence as intel
import ingestion
import decisioning
import reports

db.init_db()

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))

_uc = itertools.count(1)
def newuser():
    return f"t{next(_uc)}-{datetime.utcnow().strftime('%H%M%S%f')}"

def run(uid, text):
    """Run a text message through the router, return list of reply texts."""
    SENT.clear()
    main.process_text(uid, text)
    return [b.get("text", "") for m, b in SENT if m == "sendMessage"]

def joined(texts):
    return "\n".join(texts)

def txs(uid, ttype=None):
    if ttype:
        return db._q("SELECT amount,type,category,merchant,source FROM transactions WHERE user_id=%s AND type=%s",
                     (uid, ttype), fetch="all")
    return db._q("SELECT amount,type,category,merchant,source FROM transactions WHERE user_id=%s",
                 (uid,), fetch="all")

print(">>> Running scenarios...\n")

# ============================================================ A. INGESTION
u = newuser()
r = run(u, "ăn trưa 50k")
check("A1 manual expense recorded", any(float(t["amount"]) == 50000 and t["type"] == "expense" for t in txs(u)), str(txs(u)))
check("A1b expense confirmation reply", "Đã ghi" in joined(r), joined(r)[:80])

u = newuser()
run(u, "lương 20tr")
check("A2 manual income recorded as income tx", any(float(t["amount"]) == 20000000 and t["type"] == "income" for t in txs(u)), str(txs(u)))

u = newuser()
run(u, "thưởng tết 5tr")
check("A3 bonus -> income tx", any(t["type"] == "income" and float(t["amount"]) == 5000000 for t in txs(u)), str(txs(u)))

u = newuser()
run(u, "taxi về nhà 2tr5")
check("A4 amount '2tr5' = 2.500.000", any(float(t["amount"]) == 2500000 for t in txs(u)), str(txs(u)))

u = newuser()
run(u, "đổ xăng 100 nghìn")
check("A5 amount '100 nghìn' = 100.000", any(float(t["amount"]) == 100000 for t in txs(u)), str(txs(u)))

u = newuser()
run(u, "cà phê Highlands 55k")
check("A6 merchant captured", any((t["merchant"] or "") != "" for t in txs(u)), str(txs(u)))

u = newuser()
sms = "TK 0123456789|GD: -150,000VND luc 13/06 12:30|So du: 2,500,000VND|ND: COFFEE HIGHLAND"
check("A7a bank SMS detected", ingestion.looks_like_bank_sms(sms), "")
run(u, sms)
check("A7b bank SMS recorded as expense ~150k", any(t["type"] == "expense" and float(t["amount"]) == 150000 for t in txs(u)), str(txs(u)))

u = newuser()
csv = "date,amount,type,merchant\n2026-06-01,50000,expense,com tam\n2026-06-02,30000,expense,tra sua\n2026-06-03,20000000,income,luong"
run(u, csv)
check("A8 CSV imported 3 rows", len(txs(u)) == 3, f"got {len(txs(u))}")

u = newuser()
r = run(u, "tôi nên bắt đầu tiết kiệm như thế nào")
check("A9 no-amount -> question (no tx, has advice)", len(txs(u)) == 0 and len(joined(r)) > 20, f"tx={len(txs(u))}")

# ============================================================ B. CATEGORIZATION
cat_cases = {
    "tiền điện tháng này 500k": "hoa_don_tien_ich",
    "grab về nhà 80k": "di_chuyen",
    "mua áo trên shopee 300k": "mua_sam",
    "xem phim CGV 120k": "giai_tri",
    "mua thuốc cảm 200k": "suc_khoe",
    "đóng học phí tiếng anh 2tr": "giao_duc",
    "trả tiền thuê nhà 5tr": "nha_o",
}
for text, expected in cat_cases.items():
    uu = newuser()
    run(uu, text)
    got = (txs(uu)[0]["category"] if txs(uu) else None)
    check(f"B categorize {expected}", got == expected, f"{text!r} -> {got}")

# ============================================================ C. BUDGET TIERS + COOLDOWN
u = newuser()
r = run(u, "ngân sách ăn uống 1tr")
check("C1 set_budget stored", db.get_budget(u, "an_uong") is not None, "")
check("C1b set_budget reply", "ngân sách" in joined(r).lower(), joined(r)[:80])

# push to ~95% (950k) to trigger a warning/critical on the crossing transaction
SENT.clear(); main.process_text(u, "ăn nhà hàng 950k")
warn = joined([b.get("text", "") for m, b in SENT if m == "sendMessage"])
check("C2 budget alert fired (>=90%)", ("Ngân sách" in warn and ("⚠️" in warn or "🔴" in warn)), warn[:120])
check("C2b alert has explainability", all(k in warn for k in ("Vì sao", "Dựa trên", "Nếu làm theo")) if "Ngân sách" in warn else False, warn[:200])

# cooldown: another small expense keeps same tier -> should NOT re-alert same key
SENT.clear(); main.process_text(u, "ăn vặt 10k")
warn2 = joined([b.get("text", "") for m, b in SENT if m == "sendMessage"])
check("C3 cooldown suppresses duplicate tier alert", "Ngân sách Ăn uống" not in warn2 or warn2.count("Ngân sách") == 0, warn2[:120])

# ============================================================ D. ANOMALY
u = newuser()
for _ in range(4):
    db.add_transaction(u, 40000, "expense", category="an_uong", source="seed")
an = intel.detect_anomaly(u, 300000, "an_uong")
check("D1 anomaly detected (300k vs ~40k avg)", an is not None and an["factor"] >= 3, str(an))
alerts = decisioning.transaction_alerts(u, 300000, "an_uong", an)
check("D2 anomaly produces alert with explanation", any(a["key"].startswith("anomaly") for a in alerts), str([a["key"] for a in alerts]))

# ============================================================ E. RECURRING
u = newuser()
now = datetime.utcnow()
for d in (now, now - timedelta(days=35), now - timedelta(days=68)):
    db.add_transaction(u, 180000, "expense", merchant="Netflix", category="giai_tri", source="seed", ts=d)
rec = intel.detect_recurring(u)
check("E1 recurring detected (Netflix x3 months)", any("netflix" in x["merchant"] for x in rec), str(rec))
r = run(u, "hóa đơn định kỳ")
check("E2 recurring view reply", "định kỳ" in joined(r).lower() or "netflix" in joined(r).lower(), joined(r)[:120])

# ============================================================ F. GOALS
u = newuser()
r = run(u, "mục tiêu mua xe 50tr")
check("F1 goal created", len(db.list_goals(u)) == 1, str(db.list_goals(u)))
r = run(u, "tiết kiệm cho mua xe 5tr")
g = db.list_goals(u)[0]
check("F2 add_to_goal updates saved", float(g["saved_amount"]) == 5000000, str(dict(g)))
r = run(u, "mục tiêu của tôi")
check("F3 view goals reply", "Mua xe" in joined(r) or "mục tiêu" in joined(r).lower(), joined(r)[:120])

# ============================================================ G. SET_INCOME (baseline, NOT a tx)
u = newuser()
run(u, "thu nhập hàng tháng 25tr")
st = db.get_settings(u)
check("G1 monthly_income stored in settings", st and st.get("monthly_income") and float(st["monthly_income"]) == 25000000, str(dict(st) if st else None))
check("G2 set_income did NOT create a tx", len(txs(u)) == 0, f"tx={len(txs(u))}")

# ============================================================ H. REPORTS
u = newuser()
run(u, "lương 20tr"); run(u, "ăn trưa 60k")
r = run(u, "báo cáo")
check("H1 weekly report label", "tài chính tuần" in joined(r), joined(r)[:80])
check("H1b income shown in report", "20.000.000" in joined(r), joined(r)[:160])
r = run(u, "báo cáo tháng này")
check("H2 monthly report label", "tháng này" in joined(r), joined(r)[:80])

# ============================================================ I. VIEWS / MISC INTENTS
u = newuser()
run(u, "ngân sách ăn uống 2tr"); run(u, "ăn trưa 100k")
check("I1 view_budgets", "Ngân sách" in joined(run(u, "ngân sách của tôi")), "")
check("I2 forecast", "Dự báo" in joined(run(u, "dự báo")), "")
check("I3 recent", "gần nhất" in joined(run(u, "lịch sử")).lower() or "giao dịch" in joined(run(u, "lịch sử")).lower(), "")
check("I4 help", "trợ lý" in joined(run(u, "help")).lower(), "")

# ============================================================ J. ROUTING / EDGE (handler-level)
def hres(payload):
    return main.handler(payload, type("C", (), {"session_id": None, "user_id": None, "request_headers": {}})())

check("J1 cron wrong secret -> unauthorized",
      hres({"cron": "alerts", "secret": "nope"}).get("ok") is False, "")
check("J2 cron right secret -> ok",
      hres({"cron": "alerts", "secret": main.config.CRON_SECRET}).get("ok") is True
      if main.config.CRON_SECRET else hres({"cron": "alerts"}).get("ok") is True, "")
check("J3 bot sender skipped",
      hres({"event_name": "message.text.received",
            "message": {"chat": {"id": "x"}, "text": "hi", "from": {"id": "x", "is_bot": True}}}).get("skipped") == "ignored", "")
check("J4 unknown event skipped",
      "skipped" in hres({"event_name": "message.sticker.received",
                         "message": {"chat": {"id": "x"}, "from": {"id": "x"}}}), "")
check("J5 empty payload skipped", "skipped" in hres({"foo": "bar"}), "")

# ============================================================ K. LONG REPLY SPLIT
parts = channel_zalo.split_message("A" * 5000)
check("K1 long message split <=2000", len(parts) >= 3 and all(len(p) <= 2000 for p in parts), f"{len(parts)} parts")

# ============================================================ L. DEDUP (OCR/SMS)
from datetime import datetime as _dt
u = newuser()
ocr = {"amount": 420440, "type": "expense", "merchant": "Shopee", "note": "OCR",
       "ts": _dt(2026, 6, 14), "source": "ocr", "raw": "img-A"}
SENT.clear(); main._record_and_react(u, u, dict(ocr))
SENT.clear(); main._record_and_react(u, u, dict(ocr))  # resend same receipt
dup_msg = joined([b.get("text", "") for m, b in SENT if m == "sendMessage"])
check("L1 duplicate OCR not re-recorded",
      len([t for t in txs(u) if float(t["amount"]) == 420440]) == 1,
      f"count={len([t for t in txs(u) if float(t['amount'])==420440])}")
check("L2 duplicate informs user", "trùng" in dup_msg.lower(), dup_msg[:100])
SENT.clear(); main._record_and_react(u, u, {"amount": 99000, "type": "expense",
              "merchant": "Shopee", "ts": _dt(2026, 6, 14), "source": "ocr", "raw": "img-B"})
check("L3 different amount still records", any(float(t["amount"]) == 99000 for t in txs(u)), "")
u2 = newuser()
SENT.clear(); main._record_and_react(u2, u2, {"amount": 50000, "type": "expense",
              "merchant": "cafe", "ts": None, "source": "manual", "raw": "a"})
SENT.clear(); main._record_and_react(u2, u2, {"amount": 50000, "type": "expense",
              "merchant": "cafe", "ts": None, "source": "manual", "raw": "b"})
check("L4 manual duplicates allowed (not deduped)",
      len([t for t in txs(u2) if float(t["amount"]) == 50000]) == 2, "")

# ============================================================ M. UNDO / DELETE
u = newuser()
db.add_transaction(u, 75000, "expense", category="an_uong", source="seed")
db.add_transaction(u, 30000, "expense", category="an_uong", source="seed")
before = len(txs(u))
r = run(u, "xóa giao dịch gần nhất")
check("M1 undo deletes one tx", len(txs(u)) == before - 1, f"{before}->{len(txs(u))}")
check("M2 undo confirms deletion", "đã xóa" in joined(r).lower(), joined(r)[:100])
u2 = newuser()
r = run(u2, "hủy giao dịch")
check("M3 undo with no tx -> friendly msg", "không có" in joined(r).lower(), joined(r)[:80])
check("M4 'xóa giao dịch gần nhất' routes to undo (not recent)",
      main._fast_intent("xóa giao dịch gần nhất") == "undo", str(main._fast_intent("xóa giao dịch gần nhất")))
check("M5 'lịch sử' still routes to recent",
      main._fast_intent("lịch sử") == "recent", str(main._fast_intent("lịch sử")))
check("M6 'hoàn tác' routes to undo", main._fast_intent("hoàn tác") == "undo", "")
# Entered order, not business date: income (ts=now) first, then OCR receipt with a
# PAST date entered last -> undo must target the OCR receipt (entered most recently).
u = newuser()
db.add_transaction(u, 20000000, "income", category="thu_nhap", source="manual")
db.add_transaction(u, 420440, "expense", merchant="Shopee", category="mua_sam",
                   source="ocr", ts=_dt(2026, 6, 14))
last = db.get_last_transaction(u)
check("M7 last = most recently entered (not earliest ts)",
      last and float(last["amount"]) == 420440, str(dict(last)) if last else None)

# ============================================================ N. WELCOME (new user)
u = newuser()
r = run(u, "chào")
check("N1 new user gets welcome", "Chào mừng" in joined(r), joined(r)[:60])
r2 = run(u, "lịch sử")
check("N2 returning user NOT re-welcomed", "Chào mừng" not in joined(r2), joined(r2)[:60])
u3 = newuser()
r3 = run(u3, "ăn trưa 50k")
check("N3 new user + transaction: welcomed AND recorded",
      "Chào mừng" in joined(r3) and any(float(t["amount"]) == 50000 for t in txs(u3)),
      joined(r3)[:60])

# ============================================================ SUMMARY
print("\n================ RESULTS ================")
passed = sum(1 for _, ok, _ in RESULTS if ok)
for name, ok, detail in RESULTS:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if not ok and detail:
        line += f"  -- {detail[:160]}"
    print(line)
print(f"\n{passed}/{len(RESULTS)} passed, {len(RESULTS)-passed} failed")

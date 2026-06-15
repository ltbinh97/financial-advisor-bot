"""Ingestion layer — normalize raw inputs into transaction drafts.

A *draft* is a dict: {amount, type, merchant, note, ts, source, raw}.
Category is assigned later by the intelligence layer.
"""

import csv
import io
import logging
import re
from datetime import datetime

import llm

logger = logging.getLogger("pfm.ingest")

_AMOUNT_HINT = (
    "Quy ước số tiền tiếng Việt: 'k'/'nghìn'=*1.000; 'tr'/'triệu'=*1.000.000; "
    "'2tr5'=2.500.000; 'tỷ'=*1.000.000.000. type='income' nếu là thu nhập/lương/được nhận, "
    "ngược lại 'expense'."
)


def parse_manual_text(text: str) -> dict | None:
    """Parse a free-text manual entry like 'ăn trưa 50k' or 'lương 15tr'."""
    res = llm.chat_json(
        "Bạn trích xuất một giao dịch tài chính từ câu tiếng Việt. " + _AMOUNT_HINT +
        " Trả JSON: {\"amount\": number (VND, >0), \"type\": \"income|expense\", "
        "\"merchant\": string|null, \"note\": string|null}. "
        "Nếu KHÔNG có số tiền rõ ràng, trả {\"amount\": 0}.",
        text, max_tokens=200)
    if not res or not isinstance(res, dict):
        return None
    try:
        amount = float(res.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    ttype = res.get("type") if res.get("type") in ("income", "expense") else "expense"
    return {
        "amount": amount, "type": ttype,
        "merchant": res.get("merchant"), "note": res.get("note"),
        "ts": None, "source": "manual", "raw": text,
    }


def parse_receipt_image(image_url: str) -> dict | None:
    """OCR a receipt photo via the vision model into an expense draft."""
    res = llm.vision_json(
        "Đây là ảnh hóa đơn/biên lai. Trích xuất JSON: "
        "{\"amount\": number (tổng tiền VND), \"merchant\": string|null, "
        "\"date\": \"YYYY-MM-DD\"|null, \"note\": string|null}. "
        "Chỉ trả JSON.",
        image_url, max_tokens=400)
    if not res or not isinstance(res, dict):
        return None
    try:
        amount = float(res.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    ts = None
    if res.get("date"):
        try:
            ts = datetime.strptime(res["date"], "%Y-%m-%d")
        except ValueError:
            ts = None
    return {
        "amount": amount, "type": "expense",
        "merchant": res.get("merchant"), "note": res.get("note") or "Hóa đơn (OCR)",
        "ts": ts, "source": "ocr", "raw": image_url,
    }


# Heuristic: looks like a bank transaction SMS/notification.
_BANK_HINT = re.compile(
    r"(s[ốo]\s*d[ưu]|TK|tài kho[ảa]n|VND|\+|-)\s*[\d.,]+", re.IGNORECASE)


def looks_like_bank_sms(text: str) -> bool:
    t = text.lower()
    return bool(_BANK_HINT.search(text)) and any(
        k in t for k in ["số dư", "so du", "tk", "tài khoản", "tai khoan", "vcb", "techcombank",
                          "biến động", "bien dong", "gd:", "giao dịch"])


def parse_bank_sms(text: str) -> dict | None:
    """Parse a bank notification SMS into a draft (LLM-assisted)."""
    res = llm.chat_json(
        "Đây là tin nhắn biến động số dư ngân hàng. " + _AMOUNT_HINT +
        " Trích JSON: {\"amount\": number (VND, >0, số tiền giao dịch không phải số dư), "
        "\"type\": \"income|expense\" (tiền ra/-/GD trừ = expense, tiền vào/+ = income), "
        "\"merchant\": string|null}. Nếu không phải giao dịch, trả {\"amount\": 0}.",
        text, max_tokens=200)
    if not res or not isinstance(res, dict):
        return None
    try:
        amount = float(res.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    ttype = res.get("type") if res.get("type") in ("income", "expense") else "expense"
    return {
        "amount": amount, "type": ttype, "merchant": res.get("merchant"),
        "note": "Từ SMS ngân hàng", "ts": None, "source": "bank_sms", "raw": text,
    }


def parse_csv(text: str) -> list:
    """Parse CSV text. Expected headers (flexible): date, amount, type, merchant/note.

    Returns a list of drafts. Positive amount with type missing -> expense;
    a leading '-' or type=income is respected.
    """
    drafts = []
    try:
        reader = csv.DictReader(io.StringIO(text.strip()))
        for row in reader:
            row = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
            raw_amt = row.get("amount") or row.get("so_tien") or row.get("số tiền") or ""
            raw_amt = raw_amt.replace(",", "").replace(".", "") if raw_amt.count(",") or raw_amt.count(".") > 1 else raw_amt.replace(",", "")
            try:
                amount = float(re.sub(r"[^\d.-]", "", raw_amt))
            except (TypeError, ValueError):
                continue
            if amount == 0:
                continue
            ttype = (row.get("type") or row.get("loai") or "").lower()
            if ttype not in ("income", "expense"):
                ttype = "income" if amount > 0 and ("thu" in ttype or "income" in ttype) else "expense"
            ts = None
            datestr = row.get("date") or row.get("ngay") or row.get("ngày")
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                if datestr:
                    try:
                        ts = datetime.strptime(datestr, fmt); break
                    except ValueError:
                        continue
            drafts.append({
                "amount": abs(amount), "type": ttype,
                "merchant": row.get("merchant") or row.get("note") or row.get("ghi chú") or row.get("ghi chu"),
                "note": row.get("note") or row.get("ghi chú") or row.get("ghi chu"),
                "ts": ts, "source": "csv", "raw": str(row),
            })
    except Exception:
        logger.exception("CSV parse failed")
    return drafts

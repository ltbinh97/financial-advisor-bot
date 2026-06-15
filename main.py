"""Personal Finance Advisor agent for GreenNode AgentBase.

Receives Zalo Bot webhook events at POST /invocations (SDK convention),
generates a financial-advice reply with an LLM (GreenNode AI Platform),
and sends the reply back to the user through the Zalo Bot API.

Platform hard requirements satisfied: listens on :8080 and exposes GET /health.
"""

import os
import logging
import threading

import requests
from openai import OpenAI
from dotenv import load_dotenv
from greennode_agentbase import (
    GreenNodeAgentBaseApp,
    RequestContext,
    PingStatus,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("finance-advisor")

# --- Configuration (injected via environment / .env) ---------------------------
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL")
LLM_MODEL = os.environ.get("LLM_MODEL")
ZALO_BOT_TOKEN = os.environ.get("ZALO_BOT_TOKEN")
ZALO_API_BASE = os.environ.get("ZALO_API_BASE", "https://bot-api.zaloplatforms.com")

llm = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

SYSTEM_PROMPT = (
    "Bạn là trợ lý tư vấn tài chính cá nhân thân thiện tên là \"Cố vấn Tài chính "
    "Dreamland\". Bạn giúp người Việt quản lý chi tiêu, lập ngân sách, tiết kiệm, "
    "trả nợ, quỹ dự phòng và kiến thức đầu tư cơ bản.\n"
    "Nguyên tắc trả lời:\n"
    "- Dùng tiếng Việt, NGẮN GỌN (tối đa ~1500 ký tự, 4–6 gạch đầu dòng), rõ ràng, thực tế.\n"
    "- Khi phù hợp hãy đưa con số/tỷ lệ tham khảo cụ thể (vd nguyên tắc 50/30/20).\n"
    "- KHÔNG hứa hẹn lợi nhuận, không khuyên đầu tư mạo hiểm; luôn nhắc tới rủi ro.\n"
    "- Nếu câu hỏi nằm ngoài lĩnh vực tài chính cá nhân, lịch sự hướng người dùng "
    "quay lại chủ đề tài chính.\n"
    "- Với quyết định tài chính lớn, khuyên người dùng tham vấn chuyên gia được cấp phép."
)


def ask_llm(user_text: str) -> str:
    """Generate a financial-advice reply. Qwen thinking-mode is disabled for speed."""
    resp = llm.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        max_tokens=600,
        temperature=0.7,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return (resp.choices[0].message.content or "").strip()


ZALO_MAX_LEN = 2000  # Zalo sendMessage rejects text > 2000 characters.


def zalo_call(method: str, body: dict) -> dict:
    url = f"{ZALO_API_BASE}/bot{ZALO_BOT_TOKEN}/{method}"
    resp = requests.post(url, json=body, timeout=20)
    return resp.json()


def split_message(text: str, limit: int = ZALO_MAX_LEN) -> list:
    """Split text into chunks <= limit chars, preferring paragraph/line/space breaks."""
    chunks = []
    remaining = text.strip()
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer to break on the last paragraph, then newline, then space.
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def zalo_send_text(chat_id: str, text: str) -> None:
    """Send a (possibly long) reply to Zalo, split into <=2000-char messages."""
    for part in split_message(text):
        result = zalo_call("sendMessage", {"chat_id": chat_id, "text": part})
        if not result.get("ok"):
            logger.error("Zalo sendMessage failed: %s", result)


def process_and_reply(chat_id: str, text: str) -> None:
    """Run in a background thread so the webhook returns 200 immediately."""
    try:
        zalo_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        logger.warning("sendChatAction failed", exc_info=True)

    try:
        reply = ask_llm(text)
    except Exception:
        logger.exception("LLM call failed")
        reply = "Xin lỗi, hiện mình chưa trả lời được. Bạn vui lòng thử lại sau nhé."

    if not reply:
        reply = "Mình chưa rõ ý bạn. Bạn mô tả rõ hơn vấn đề tài chính đang gặp nhé."

    try:
        zalo_send_text(chat_id, reply)
    except Exception:
        logger.exception("Zalo sendMessage error")


app = GreenNodeAgentBaseApp()


@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    """Handle an incoming Zalo Bot webhook event."""
    event = payload.get("event_name")
    message = payload.get("message") or {}

    # Only react to inbound text messages from real users.
    if event != "message.text.received":
        return {"ok": True, "skipped": event or "no_event"}

    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    sender = message.get("from") or {}

    if sender.get("is_bot"):
        return {"ok": True, "skipped": "from_bot"}
    if not chat_id or not text:
        return {"ok": True, "skipped": "empty"}

    # Reply asynchronously to avoid Zalo webhook timeouts / retries.
    threading.Thread(
        target=process_and_reply, args=(chat_id, text), daemon=True
    ).start()
    return {"ok": True}


@app.ping
def health_check() -> PingStatus:
    return PingStatus.HEALTHY


if __name__ == "__main__":
    app.run(port=8080, host="0.0.0.0")

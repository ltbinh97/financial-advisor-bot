"""Zalo Bot API channel adapter."""

import logging
import requests

import config

logger = logging.getLogger("pfm.zalo")


def _call(method: str, body: dict) -> dict:
    url = f"{config.ZALO_API_BASE}/bot{config.ZALO_BOT_TOKEN}/{method}"
    resp = requests.post(url, json=body, timeout=20)
    return resp.json()


def typing(chat_id: str) -> None:
    try:
        _call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        logger.warning("sendChatAction failed", exc_info=True)


def split_message(text: str, limit: int = config.ZALO_MAX_LEN):
    chunks, remaining = [], text.strip()
    while len(remaining) > limit:
        window = remaining[:limit]
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


def send_text(chat_id: str, text: str) -> None:
    """Send a possibly long reply, split into <=2000-char messages."""
    for part in split_message(text):
        result = _call("sendMessage", {"chat_id": chat_id, "text": part})
        if not result.get("ok"):
            logger.error("Zalo sendMessage failed: %s", result)

"""Central configuration loaded from environment / .env."""

import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM (GreenNode AI Platform, OpenAI-compatible) ---
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3-5-27b")
# Vision-capable model for OCR (multimodal). Defaults to the chat model.
VISION_MODEL = os.environ.get("VISION_MODEL", LLM_MODEL)

# --- Zalo Bot ---
ZALO_BOT_TOKEN = os.environ.get("ZALO_BOT_TOKEN")
ZALO_API_BASE = os.environ.get("ZALO_API_BASE", "https://bot-api.zaloplatforms.com")
ZALO_MAX_LEN = 2000  # sendMessage hard limit

# --- Storage ---
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- Cron protection ---
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# --- Domain defaults ---
DEFAULT_CURRENCY = "VND"
# Multi-tier budget alert thresholds (fraction of budget used).
ALERT_TIERS = [
    ("info", 0.70),
    ("warning", 0.90),
    ("critical", 1.00),
]
# Minimum hours between repeating the same alert (anti-spam cooldown).
ALERT_COOLDOWN_HOURS = 12
# A single transaction this many times the 30-day category average is an anomaly.
ANOMALY_FACTOR = 3.0

# Canonical spending categories (auto-categorization maps into these).
CATEGORIES = [
    "an_uong",          # ăn uống
    "di_chuyen",        # di chuyển / xăng xe
    "hoa_don_tien_ich", # hóa đơn & tiện ích (điện, nước, internet)
    "mua_sam",          # mua sắm
    "giai_tri",         # giải trí
    "suc_khoe",         # sức khỏe / y tế
    "giao_duc",         # giáo dục
    "nha_o",            # nhà ở / thuê nhà
    "thu_nhap",         # thu nhập (income)
    "tiet_kiem_dau_tu", # tiết kiệm / đầu tư
    "khac",             # khác
]

CATEGORY_LABELS = {
    "an_uong": "Ăn uống",
    "di_chuyen": "Di chuyển",
    "hoa_don_tien_ich": "Hóa đơn & tiện ích",
    "mua_sam": "Mua sắm",
    "giai_tri": "Giải trí",
    "suc_khoe": "Sức khỏe",
    "giao_duc": "Giáo dục",
    "nha_o": "Nhà ở",
    "thu_nhap": "Thu nhập",
    "tiet_kiem_dau_tu": "Tiết kiệm/Đầu tư",
    "khac": "Khác",
}

# financial-advisor-bot

Trợ lý **quản lý tài chính cá nhân (PFM)** tương tác qua **Zalo Bot**, chạy trên **GreenNode AgentBase**,
lưu dữ liệu ở **PostgreSQL**, dùng **LLM GreenNode AI Platform**.

Theo dõi thu/chi, phân loại giao dịch, lập ngân sách, đặt mục tiêu tiết kiệm, cảnh báo đa tầng
và báo cáo định kỳ — mọi khuyến nghị đều kèm *vì sao / dựa trên dữ liệu nào / ảnh hưởng gì*.

## Kiến trúc 4 lớp

| Lớp | Module | Nhiệm vụ |
|-----|--------|----------|
| **Ingestion** | `ingestion.py` | Nhận giao dịch: text thủ công, OCR hóa đơn (vision), SMS ngân hàng, CSV |
| **Intelligence** | `intelligence.py` | Auto-categorize, phát hiện bất thường, hóa đơn định kỳ, dự báo dòng tiền, rủi ro vượt chi |
| **Decisioning** | `decisioning.py` | Chọn alert nào/độ khẩn/kênh/thời điểm theo ngưỡng đa tầng (70/90/100%), chống spam |
| **Explainability** | `explain.py` | Mọi khuyến nghị/alert kèm `why` · `evidence` · `impact` |

Hỗ trợ: `main.py` (router webhook + cron), `db.py` (Postgres), `llm.py` (chat/JSON/vision),
`channel_zalo.py` (gửi tin, tách ≤2000 ký tự), `reports.py` (báo cáo + insight).

```
User (Zalo) ─► webhook ─► AgentBase Runtime /invocations ─► Ingestion → Intelligence → Decisioning
                                              │                                   │
                                         Postgres (state)                     Zalo reply (+ explain)
Scheduler ─► POST {"cron":"alerts|weekly","secret":...} ─► quét alert / gửi báo cáo định kỳ
```

## Tính năng

- Ghi giao dịch: `"ăn trưa 50k"`, `"lương 15tr"`, dán **SMS ngân hàng**, gửi **ảnh hóa đơn** (OCR), dán **CSV**
- Ngân sách: `"ngân sách ăn uống 3tr"` → cảnh báo 70/90/100%
- Mục tiêu: `"mục tiêu mua xe 50tr"`, `"tiết kiệm cho mua xe 1tr"`
- Báo cáo: `"báo cáo"` (thu/chi/ròng, top danh mục, ngân sách, mục tiêu, dự báo, nhận định)
- Xem: `"ngân sách của tôi"`, `"mục tiêu của tôi"`, `"hóa đơn định kỳ"`, `"dự báo"`, `"lịch sử"`
- Hỏi đáp tư vấn tài chính tự do

## Biến môi trường

| Biến | Mô tả |
|------|-------|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | LLM GreenNode AIP (OpenAI-compatible) |
| `VISION_MODEL` | Model đa phương thức cho OCR (mặc định = `LLM_MODEL`) |
| `DATABASE_URL` | Postgres, vd `postgresql://...?sslmode=require` |
| `ZALO_BOT_TOKEN` | Token bot Zalo |
| `CRON_SECRET` | Bí mật bảo vệ endpoint cron |

> `GREENNODE_*` được AgentBase Runtime tự inject. Schema DB tự tạo khi khởi động (`init_db()`).

## Chạy local

```bash
cp .env.example .env   # điền giá trị thật (gồm DATABASE_URL Postgres)
docker build -t financial-advisor-bot .
docker run --rm --env-file .env -p 8080:8080 financial-advisor-bot
curl localhost:8080/health
```

## Deploy

Dùng bộ skill GreenNode AgentBase trong `.claude/skills/` (build → push CR → tạo/ update runtime),
đăng ký Zalo webhook `{endpoint}/invocations` (kèm `secret_token`), và cắm scheduler gọi `/invocations`
với `{"cron":"alerts|weekly","secret":CRON_SECRET}`.

## Lưu ý

- LLM dạng "thinking" (Qwen 3.x): tắt reasoning bằng `extra_body={"chat_template_kwargs":{"enable_thinking":False}}`.
- Zalo `sendMessage` giới hạn 2000 ký tự — câu trả lời dài tự tách nhiều tin. `setWebhook` cần `secret_token`.

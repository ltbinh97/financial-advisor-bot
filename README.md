# financial-advisor-bot

AI agent **tư vấn tài chính cá nhân** tương tác qua **Zalo Bot**, chạy trên **GreenNode AgentBase**.

Người dùng nhắn tin cho bot Zalo → webhook gọi vào runtime → agent dùng LLM (GreenNode AI Platform)
sinh lời tư vấn tài chính tiếng Việt → trả lời lại qua Zalo Bot API.

## Kiến trúc

```
User (Zalo) ──► Zalo Bot webhook ──► AgentBase Runtime (/invocations)
                                          │
                                          ├─► LLM (GreenNode AIP, OpenAI-compatible)
                                          └─► Zalo sendMessage (trả lời, tự tách ≤2000 ký tự)
```

- **Custom Agent** (Docker image) deploy lên `/agent-runtimes`, port `8080`, health `GET /health`.
- Webhook Zalo trỏ về `{endpoint}/invocations`; trả `{"ok": true}` ngay, xử lý LLM + gửi trả lời bất đồng bộ để tránh timeout webhook.

## Cấu trúc

- `main.py` — entrypoint: handler webhook Zalo, gọi LLM, gửi trả lời (tách tin dài).
- `Dockerfile` — image `python:3.11-slim`.
- `requirements.txt` — `greennode-agentbase`, `openai`, `requests`, `python-dotenv`.
- `.env.example` — mẫu biến môi trường (KHÔNG commit `.env` thật).

## Biến môi trường

| Biến | Mô tả |
|------|-------|
| `LLM_API_KEY` | API key GreenNode AI Platform |
| `LLM_BASE_URL` | `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1` |
| `LLM_MODEL` | vd `qwen/qwen3-5-27b` |
| `ZALO_BOT_TOKEN` | Token bot Zalo từ OA "Zalo Bot Manager" |

> `GREENNODE_CLIENT_ID/SECRET/AGENT_IDENTITY/ENDPOINT_URL` được AgentBase Runtime tự inject — không đặt thủ công.

## Chạy local

```bash
cp .env.example .env   # điền giá trị thật
docker build -t financial-advisor-bot .
docker run --rm --env-file .env -p 8080:8080 financial-advisor-bot
# health: curl localhost:8080/health
```

## Deploy

Dùng bộ skill GreenNode AgentBase trong `.claude/skills/` (build → push CR → tạo runtime), sau đó đăng ký
Zalo webhook trỏ về `{endpoint}/invocations` (kèm `secret_token`).

## Lưu ý LLM

Model dạng "thinking" (Qwen 3.x) cần tắt reasoning để không tiêu hết token:
`extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.

## Lưu ý Zalo

`sendMessage` giới hạn 2000 ký tự — agent tự tách câu trả lời dài thành nhiều tin.
`setWebhook` bắt buộc kèm `secret_token`.

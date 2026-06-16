# 📖 Hướng dẫn sử dụng — Trợ lý Tài chính Cá nhân (Zalo)

Trợ lý giúp bạn **ghi chép thu chi, lập ngân sách, đặt mục tiêu tiết kiệm, nhận cảnh báo và xem báo cáo** — tất cả bằng cách **nhắn tin tự nhiên** trên Zalo, không cần cú pháp cứng nhắc.

> Mọi cảnh báo/khuyến nghị đều kèm **Vì sao · Dựa trên dữ liệu nào · Ảnh hưởng gì** để bạn hiểu rõ trước khi quyết định.

---

## 🚀 Bắt đầu nhanh (người dùng mới)

1. **Mở Zalo** và vào cuộc trò chuyện với bot (vd *Bot Dreamland*).
2. Gõ **`help`** (hoặc `menu`, `bắt đầu`) để xem nhanh bot làm được gì.
3. Làm theo 5 bước onboarding dưới đây — chỉ mất ~2 phút.

### 5 bước thiết lập lần đầu

| Bước | Gõ thử | Bot làm gì |
|------|--------|-----------|
| 1️⃣ Khai báo thu nhập | `thu nhập hàng tháng 20tr` | Lưu mức thu nhập cố định để tính tỷ lệ tiết kiệm |
| 2️⃣ Đặt ngân sách | `ngân sách ăn uống 3tr` | Đặt hạn mức/tháng, bật cảnh báo 70/90/100% |
| 3️⃣ Ghi giao dịch | `ăn trưa 50k` | Tự phân loại & lưu khoản chi |
| 4️⃣ Đặt mục tiêu | `mục tiêu mua xe 50tr` | Tạo mục tiêu tiết kiệm để theo dõi |
| 5️⃣ Xem báo cáo | `báo cáo tháng này` | Tổng kết thu/chi, ngân sách, mục tiêu, dự báo |

Sau đó, mỗi ngày bạn chỉ cần **nhắn lại khi có giao dịch** (vd `cà phê 45k`) — bot lo phần còn lại.

---

## 💸 Ghi giao dịch

Cứ nhắn tự nhiên, có **số tiền** là được. Bot tự nhận biết thu/chi và **tự phân loại**.

**Cách viết số tiền:**
- `50k` = 50.000 · `100 nghìn` = 100.000
- `3tr` / `3 triệu` = 3.000.000 · `2tr5` = 2.500.000
- `1 tỷ` = 1.000.000.000

**Ví dụ chi tiêu:**
```
ăn trưa 50k
cà phê Highlands 45k
đổ xăng 100 nghìn
grab về nhà 80k
tiền điện 500k
mua áo shopee 300k
```

**Ví dụ thu nhập** (lương, thưởng, được nhận...):
```
lương 20tr
thưởng tết 5tr
```
> ⚠️ Phân biệt: `lương 20tr` = một khoản **vừa nhận** (ghi vào "Thu"). Còn `thu nhập hàng tháng 20tr` = **khai báo mức cố định** (không tính là giao dịch).

Bot xác nhận lại, ví dụ: `✅ Đã ghi: －50.000đ · Ăn uống`.

### 🧾 Gửi ảnh hóa đơn (OCR)
Chụp/gửi **ảnh hóa đơn, biên lai** → bot tự đọc tổng tiền và ghi nhận khoản chi. *(Mẹo: chụp rõ phần tổng tiền.)*

### 🏦 Dán tin nhắn ngân hàng
Copy **SMS/thông báo biến động số dư** dán vào chat → bot tự bóc số tiền và tiền ra/vào.

### 📑 Nhập hàng loạt bằng CSV
Dán nhiều dòng theo định dạng (dòng đầu là tiêu đề):
```
date,amount,type,merchant
2026-06-01,50000,expense,com tam
2026-06-02,20000000,income,luong
```

---

## 🗂️ Các nhóm phân loại

Ăn uống · Di chuyển · Hóa đơn & tiện ích · Mua sắm · Giải trí · Sức khỏe · Giáo dục · Nhà ở · Thu nhập · Tiết kiệm/Đầu tư · Khác

Bot tự gán nhóm dựa trên nội dung. Nếu không chắc, nó xếp vào **Khác**.

---

## 🎯 Ngân sách & cảnh báo

**Đặt ngân sách tháng cho một nhóm:**
```
ngân sách ăn uống 3tr
ngân sách di chuyển 1tr5
```

**Cảnh báo đa tầng** (tự gửi khi bạn chi tới ngưỡng):
- ℹ️ **70%** — nhắc nhẹ
- ⚠️ **90%** — cảnh báo
- 🔴 **100%+** — đã vượt

Bot **không spam**: mỗi mốc chỉ nhắc lại sau ít nhất 12 giờ. Ngoài ra bot còn cảnh báo **giao dịch bất thường** (cao gấp ≥3× mức trung bình) và **dự báo vượt ngân sách** nếu giữ nhịp chi hiện tại.

**Xem tình trạng ngân sách:** `ngân sách của tôi`

---

## 🐖 Mục tiêu tiết kiệm

```
mục tiêu mua xe 50tr          → tạo mục tiêu
tiết kiệm cho mua xe 5tr      → bỏ thêm tiền vào mục tiêu
mục tiêu của tôi              → xem tiến độ (vd 10%)
```

**Lộ trình đạt mục tiêu:** hỏi *"tôi muốn có 2 tỷ để mua nhà, bao lâu thì đạt?"* — bot dựa trên thu/chi hàng tháng của bạn để tính **còn bao nhiêu năm/tháng/ngày**, và mức cần để dành mỗi tháng nếu muốn đạt sớm hơn (3/5/10 năm).

---

## 📊 Báo cáo & tra cứu

| Gõ | Kết quả |
|----|---------|
| `báo cáo` | Tổng kết **7 ngày** gần nhất |
| `báo cáo tháng này` | Tổng kết **từ đầu tháng**: thu/chi/ròng, top chi tiêu, ngân sách, mục tiêu, dự báo + nhận định |
| `tôi còn bao nhiêu tiền` / `số dư` | Số dư hiện tại = tổng thu − tổng chi (đã ghi) |
| `dự báo` | Dự báo tổng chi cả tháng theo nhịp hiện tại |
| `hóa đơn định kỳ` | Các khoản lặp lại hàng tháng bot phát hiện |
| `lịch sử` | 10 giao dịch gần nhất |
| `xóa giao dịch gần nhất` / `hủy giao dịch` / `hoàn tác` | Xóa giao dịch vừa ghi (nếu nhập nhầm) |

> 🗑️ **Xóa nhầm?** Gõ `xóa giao dịch gần nhất` (hoặc `hủy giao dịch`, `hoàn tác`) — bot xóa khoản mới nhất và cập nhật lại số dư/ngân sách. Giao dịch nhập tay trùng nhau vẫn được giữ; chỉ ảnh hóa đơn/SMS gửi lại y hệt mới bị tự bỏ qua.

---

## 💬 Hỏi đáp tư vấn

Hỏi bất cứ điều gì về tài chính cá nhân:
```
lương 20 triệu, muốn mua nhà sau 5 năm thì tiết kiệm thế nào?
nên để quỹ khẩn cấp bao nhiêu tháng chi tiêu?
nguyên tắc 50/30/20 là gì?
```

---

## ❓ Câu hỏi thường gặp

**Bot có hiểu tiếng Việt không dấu không?** Có, nhưng nên gõ có dấu để chính xác hơn.

**Lỡ ghi sai số tiền thì sao?** Cứ ghi lại khoản đúng; hiện chưa có lệnh xóa từng giao dịch — tính năng sẽ bổ sung sau.

**Trả lời dài có bị cắt không?** Không — bot tự tách thành nhiều tin (giới hạn 2000 ký tự/tin của Zalo).

**Dữ liệu của tôi lưu ở đâu?** Trong cơ sở dữ liệu riêng của hệ thống; mỗi người dùng được tách biệt theo tài khoản Zalo.

**Bot tự nhắn cho tôi khi nào?** Khi có cảnh báo ngân sách/giao dịch bất thường, và theo lịch báo cáo định kỳ (nếu được bật).

---

## 🧭 Tra cứu nhanh

```
help                         → menu
thu nhập hàng tháng 20tr     → khai báo thu nhập
ăn trưa 50k                  → ghi chi
lương 20tr                   → ghi thu
[gửi ảnh hóa đơn]            → OCR ghi chi
ngân sách ăn uống 3tr        → đặt ngân sách
mục tiêu mua xe 50tr         → tạo mục tiêu
tiết kiệm cho mua xe 5tr     → góp vào mục tiêu
báo cáo / báo cáo tháng này  → xem báo cáo
ngân sách của tôi            → xem ngân sách
mục tiêu của tôi             → xem mục tiêu
hóa đơn định kỳ / dự báo     → phân tích
tôi còn bao nhiêu tiền       → số dư (thu − chi); hoặc: số dư
lịch sử                      → giao dịch gần đây
xóa giao dịch gần nhất       → xóa khoản vừa ghi (hoặc: hủy giao dịch / hoàn tác)
```

Chúc bạn quản lý tài chính hiệu quả! 💪

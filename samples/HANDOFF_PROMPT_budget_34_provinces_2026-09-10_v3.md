# HANDOFF PROMPT – BATCH CRAWL NGÂN SÁCH 34 TỈNH/THÀNH VIỆT NAM
**Cập nhật đến 10/09/2026 – trạng thái mới nhất trước khi chuyển conversation**

Tiếp tục dự án **batch crawl dữ liệu công khai ngân sách 34 tỉnh/thành Việt Nam** từ trạng thái hiện tại.

## FILE PHẢI DÙNG – KHÔNG XÂY LẠI TỪ ĐẦU

1. `Source_Master_govvn_34_tinh_v4_crawl_ready(2)(1).xlsx`
   - Source Master chuẩn của 34 tỉnh/thành hiện hành.
   - Không rebuild Source Master.
   - Không crawl cấp xã/phường.

2. `Ninh_Binh_budget_compact_v6(2)(1).xlsx`
   - File mẫu tham chiếu cấu trúc/QA.
   - Không coi Ninh Bình là tỉnh production đã COMPLETE nếu chưa có xác nhận riêng.

3. `Hai_Phong_budget_full_scan_COMPLETE_2026-09-09.xlsx`
   - **FINAL production của Hải Phòng – COMPLETE 7/7.**
   - Đây là file final, không quay lại checkpoint cũ trừ khi audit/bugfix.

4. `Hue_budget_full_scan_COMPLETE_2026-09-09.xlsx`
   - **FINAL production của Huế – COMPLETE 7/7.**
   - Đây là file final, không quay lại checkpoint cũ trừ khi audit/bugfix.

---

# I. MỤC TIÊU TOÀN DỰ ÁN

Crawl dữ liệu công khai ngân sách của **34 tỉnh/thành hiện hành**, ưu tiên độ bao phủ tối đa ở cấp tỉnh:

- cổng tỉnh/thành;
- Sở Tài chính;
- toàn bộ 12 nhóm cơ quan bắt buộc;
- các Sở/ban/ngành có nguồn ngân sách;
- Ban QL KKT/KCN;
- Ban QLDA cấp tỉnh/khu vực có dữ liệu;
- đơn vị sự nghiệp trực thuộc UBND cấp tỉnh;
- quỹ tài chính nhà nước ngoài ngân sách cấp tỉnh;
- báo chí/phát thanh-truyền hình công lập cấp tỉnh;
- HĐND/Văn phòng Đoàn ĐBQH & HĐND;
- cổng văn bản/công báo;
- nguồn lịch sử của tỉnh tiền thân khi có sáp nhập.

**Không crawl xã/phường.**

Mỗi attachment phải được xử lý thực tế nếu truy cập được:
PDF/XLS/XLSX/DOC/DOCX/ZIP/RAR/7Z; archive lồng quét đệ quy; PDF scan OCR/đọc ảnh; loại trùng theo hash nếu lấy được binary.

Không tự biến blank thành 0.
Chỉ nhập 0 khi nguồn ghi rõ 0.
Nếu nguồn ghi dấu `-`, giữ nguyên `-`, không quy đổi thành 0.
Không suy đoán giá trị khi layout/text extraction không đủ chắc chắn.

---

# II. CẤU TRÚC DATA BẮT BUỘC – GIỮ NGUYÊN 16 CỘT

1. Tỉnh/TP hiện hành
2. Tỉnh/TP theo nguồn
3. Số QĐ/Văn bản
4. Ngày tài liệu
5. Kỳ dữ liệu
6. Cơ quan
7. Phạm vi
8. Nội dung/Bảng
9. Chỉ tiêu
10. Loại số liệu
11. Giá trị gốc
12. Giá trị chuẩn hóa
13. ĐVT
14. Quy đổi VND
15. Nguồn
16. Ghi chú

Quy tắc sáp nhập:
- `Tỉnh/TP hiện hành` = tên hiện tại;
- `Tỉnh/TP theo nguồn` = tên lịch sử đúng như nguồn;
- không đổi tên lịch sử hồi tố.

---

# III. CHẾ ĐỘ LÀM VIỆC MỚI – 2–3 LƯỢT/TỈNH, KHÔNG CỐ 1 TỈNH/1 LƯỢT

Đã thử mô hình 1 tỉnh = 1 lượt và bị timeout. Từ 10/09/2026, **mặc định mỗi tỉnh chia 2–3 lượt/batch vừa phải**.

## Phương án chuẩn

### Lượt 1 – CP1 + CP2 + phần chính CP3
- portal + 12 nhóm bắt buộc;
- CURRENT A/B;
- backbone ngân sách;
- các Sở/ngành chính;
- attachment nào thông thì xử lý ngay;
- **bắt buộc xuất checkpoint cuối lượt**.

### Lượt 2 – hoàn thiện CP3 + CP4 + CP5
- các Sở còn thiếu;
- BQL KKT/KCN, BQLDA, đơn vị sự nghiệp, quỹ, báo chí, HĐND/cổng văn bản;
- archive tỉnh tiền thân;
- phân loại nguồn: `FOUND / VERIFIED_NO_BUDGET_ITEM / TECH_BLOCKED / N/A`;
- **bắt buộc xuất checkpoint cuối lượt**.

### Lượt 3 – CP6 + CP7
- OCR/hash/dedupe/QA kỹ thuật;
- xử lý pending;
- chỉ áp dụng `TECH_EXCEPTION` khi có bằng chứng nguồn/file tồn tại nhưng môi trường không lấy được binary;
- Final QA → COMPLETE nếu đủ điều kiện.

## Điều chỉnh theo nguồn lực
- Tỉnh nhẹ, nguồn thông: có thể gộp còn 2 lượt.
- Web/CDN chậm: giảm batch hơn nữa, không cố nhồi.
- Mỗi lượt chỉ mở số nguồn vừa phải; khi request bắt đầu chậm/timeout thì dừng mở rộng và ghi file.
- Partial checkpoint có file tốt hơn cố nhồi rồi mất tiến độ.
- Không hứa làm nền/background.

---

# IV. 7 CHECKPOINT QA CHUẨN MỖI TỈNH

- CP1: Portal + 12 nhóm bắt buộc
- CP2: Crawl CURRENT A/B + attachment
- CP3: Dữ liệu xương sống + từng Sở/ngành
- CP4: Cơ quan đặc thù/BQLDA/đơn vị sự nghiệp/quỹ/báo chí/HĐND
- CP5: Archive tỉnh tiền thân
- CP6: OCR/hash/dedupe/QA kỹ thuật
- CP7: Final QA → COMPLETE

Chỉ đánh `COMPLETE` khi:
- 12 nhóm bắt buộc VERIFIED/N/A hợp lệ;
- cơ quan đặc thù đã rà;
- CURRENT A/B đã crawl;
- archive tiền thân đủ;
- attachment actionable pending = 0 hoặc TECH_EXCEPTION hợp lệ được ghi rõ;
- dedupe/QA sạch;
- không crawl xã/phường.

---

# V. TRẠNG THÁI FINAL HẢI PHÒNG

File: `Hai_Phong_budget_full_scan_COMPLETE_2026-09-09.xlsx`

**HẢI PHÒNG: COMPLETE 7/7**

- Data: **2,020 dòng**
- schema 16 cột đúng
- 0 duplicate logic sau dedupe kỹ thuật
- 0 giá trị số chưa chuẩn hóa
- 0 lỗi Quy đổi VND
- 11 dòng nguồn ghi `-` giữ nguyên
- 4/4 PDF scan đã xử lý
- CP1–CP7: DONE

## TECH_EXCEPTION Hải Phòng
Còn 2 XLSX VHTTDL không lấy được raw binary/hash dù nguồn/văn bản/file đã được xác minh:
- Biểu 48 kèm QĐ1538
- Biểu 49 kèm QĐ1537

Hai file này được ghi **TECH_EXCEPTION**, không giả tạo là đã hash/trích thành công. Đây không còn là actionable pending và không cần retry ở conversation mới trừ khi audit riêng.

---

# VI. TRẠNG THÁI FINAL HUẾ

File: `Hue_budget_full_scan_COMPLETE_2026-09-09.xlsx`

**HUẾ: COMPLETE 7/7**

- Data final: **280 dòng**
- CP1–CP7: DONE
- 12/12 nhóm bắt buộc đã coverage/verified theo trạng thái phù hợp
- exact duplicate: 0
- logical duplicate: 0
- giá trị số chưa chuẩn hóa: 0
- lỗi Quy đổi VND: 0
- lỗi formula/workbook: 0
- actionable pending: 0

## TECH_EXCEPTION Huế
Có **16 attachment/raw-binary TECH_EXCEPTION**: nguồn/văn bản chính thức xác minh được nhưng môi trường không lấy được raw-byte/hash ổn định.

Không coi 16 TECH_EXCEPTION là đã download/hash; giữ minh bạch trong Summary_QA.

Nguồn/backbone đã có gồm:
- ngân sách cấp tỉnh Thừa Thiên Huế 2024;
- dự toán 2025;
- thực hiện Quý I/2026;
- thực hiện 6 tháng/Quý II 2026;
- quyết toán 2025 theo NQ36/NQ-HĐND;
- dự toán 2026 theo NQ106/NQ-HĐND;
- nguồn CURRENT từ nhiều Sở/ngành và nhóm đặc thù.

Cơ cấu hiện hành đã xử lý:
- 20/08/2026 thành lập Sở Văn hóa, Thể thao và Du lịch từ hợp nhất Sở VH&TT + Sở Du lịch;
- Ban Dân tộc - Tôn giáo thuộc Sở Nội vụ, không coi là Sở độc lập.

---

# VII. TRẠNG THÁI TOÀN DỰ ÁN HIỆN TẠI

Theo chuẩn 7 checkpoint/tỉnh:

- Hải Phòng: **7/7 DONE – COMPLETE**
- Huế: **7/7 DONE – COMPLETE**
- 32 tỉnh còn lại: chưa production hoặc chưa có checkpoint production mới trong conversation này

=> **14/238 checkpoint DONE**
=> còn **224/238 checkpoint**

Tình trạng tỉnh:
- COMPLETE: **2/34** – Hải Phòng, Huế
- IN_PROGRESS: **0/34** tại thời điểm handoff này
- chưa production: **32/34**

**Quan trọng:** Lượt thử bắt đầu “tỉnh kế tiếp” sau Huế đã timeout trước khi tạo checkpoint/file có giá trị. Vì vậy **không coi tỉnh thứ ba là đã bắt đầu production**. Conversation mới phải chọn tỉnh kế tiếp từ Source Master và bắt đầu sạch theo mô hình 2–3 lượt/tỉnh.

---

# VIII. THỨ TỰ ƯU TIÊN CHO CONVERSATION MỚI

1. Mở/đọc:
   - Source Master v4;
   - Ninh Bình mẫu;
   - Hải Phòng FINAL COMPLETE;
   - Huế FINAL COMPLETE.
2. Không rebuild Source Master.
3. Không quay lại Hải Phòng/Huế trừ audit/bugfix.
4. Chọn **1 tỉnh kế tiếp chưa production** từ Source Master.
5. Bắt đầu **Lượt 1** của tỉnh đó: CP1 + CP2 + phần chính CP3.
6. Giữ workload vừa phải, không cố full-close tỉnh trong một lượt.
7. Trước khi gần hết thời lượng phải export checkpoint.
8. Lượt sau tiếp tục đúng file checkpoint mới nhất, không làm lại từ đầu.

---

# IX. MẪU BÁO CÁO TIẾN ĐỘ BẮT BUỘC SAU MỖI LƯỢT

- `TỈNH A: x/7 DONE | còn y CP`
- `Batch/lượt của tỉnh: Lượt k/2–3`
- `Data hiện tại: ... dòng`
- `Actionable pending: ...`
- `TECH_EXCEPTION: ...`
- `TOÀN DỰ ÁN: n/34 COMPLETE | m IN_PROGRESS | ...`
- `Checkpoint chuẩn còn: .../238`
- link file checkpoint/final mới nhất.

---

# X. NGUYÊN TẮC QUAN TRỌNG

- Không tự biến blank thành 0.
- Dấu `-` giữ nguyên `-`.
- Không suy số khi chưa chắc layout/ĐVT.
- Không crawl xã/phường.
- Không để một attachment timeout chặn toàn batch.
- TECH_EXCEPTION phải có căn cứ rõ, không dùng để che coverage chưa rà.
- Mỗi lượt phải ưu tiên **hoàn thành + ghi file**, không ưu tiên số lượng website mở.
- Các file FINAL Hải Phòng/Huế là nguồn production chuẩn hiện tại.


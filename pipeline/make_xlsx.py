#!/usr/bin/env python3
"""Turn the national monthly CSV into a workbook fit to send to someone else.

A bare CSV loses the two things a reader without context needs: that the `ky` column holds
THREE different series that must never be mixed, and that the published monthly revenue does
not sum to the published cumulative. So the workbook leads with a notes sheet, and the monthly
table is written as values - see the note beside it for why formulas were not used here.
"""
import csv, openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

SRC = 'output/nsnn_ca_nuoc_theo_thang.csv'
OUT = 'output/NSNN_ca_nuoc_theo_thang.xlsx'
F = 'Arial'
HDR = PatternFill('solid', fgColor='1F4E79')
WARN = PatternFill('solid', fgColor='FFF2CC')
THIN = Border(bottom=Side('thin', color='BFBFBF'))

rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))
wb = openpyxl.Workbook()

# ---------------------------------------------------------------- 1. notes, read this first
ws = wb.active
ws.title = 'Đọc trước'
NOTES = [
    ('Thu/chi ngân sách nhà nước Việt Nam theo tháng — CẢ NƯỚC', 'title'),
    ('', ''),
    ('Nguồn: Cục Thống kê (Bộ Tài chính), báo cáo tình hình kinh tế - xã hội hàng tháng, '
     'nso.gov.vn. Mỗi dòng trong sheet "Số liệu" có cột "nguon" là URL bài gốc.', ''),
    ('Phạm vi: toàn quốc, 2025-01 đến 2026-08. Đơn vị: nghìn tỷ đồng (1 nghìn tỷ = 10^12 VND). '
     'Cột "vnd" là số VND tuyệt đối.', ''),
    ('', ''),
    ('QUAN TRỌNG — cột "ky" có BA chuỗi khác nhau, KHÔNG được cộng lẫn nhau:', 'h2'),
    ('  "tháng"                    số của riêng tháng đó, như nguồn công bố trong tháng đó', ''),
    ('  "luỹ kế N tháng"           số cộng dồn từ đầu năm, như nguồn công bố', ''),
    ('  "tháng (suy từ luỹ kế)"    hiệu giữa hai mốc luỹ kế liên tiếp — do chúng tôi tính, '
     'không phải nguồn công bố', ''),
    ('', ''),
    ('QUAN TRỌNG — số THU tháng KHÔNG cộng đúng thành số luỹ kế:', 'h2'),
    ('Cộng 8 tháng đầu 2026 theo chuỗi "tháng" được 1.847,8 nghìn tỷ, trong khi nguồn công bố '
     'luỹ kế 8 tháng là 2.023,8 — hụt 176,0 (8,7%). Phía CHI thì khớp (lệch 0,3; 0,0%).', ''),
    ('Lý do: số tháng là ƯỚC công bố ngay trong tháng và không bao giờ sửa lại; số luỹ kế được '
     'ước lại và điều chỉnh tăng. Phía thu điều chỉnh nhiều, phía chi gần như không.', ''),
    ('Khuyến nghị: dùng chuỗi "tháng" để xem THỜI ĐIỂM dòng tiền; dùng "tháng (suy từ luỹ kế)" '
     'để xem QUY MÔ, nhất là phía thu.', ''),
    ('', ''),
    ('Các lưu ý khác:', 'h2'),
    ('  - Mọi số đều là ƯỚC TÍNH ("ước đạt" trong nguyên văn), không phải quyết toán.', ''),
    ('  - Tháng 9/2026 chưa có: số tháng N xuất hiện trong báo cáo tháng N+1.', ''),
    ('  - Nguồn có một mâu thuẫn nội tại: 2026 tháng 1 và tháng 2 đều công bố chi 163,0 nhưng '
     'luỹ kế 2 tháng công bố 311,0 (phần lớn hơn tổng 15,0). Giữ nguyên như công bố.', ''),
    ('  - Không số nào bị sửa, làm tròn lại hay nội suy. Ô trống nghĩa là nguồn không công bố, '
     'KHÔNG phải bằng 0.', ''),
    ('', ''),
    ('Đã đối chiếu với nguồn độc lập (báo chí, Bộ Tài chính), khớp tuyệt đối: thu T1/2026 = '
     '370,7 · chi T1/2026 = 163,0 · chi T6/2026 = 292,3.', ''),
]
for i, (text, kind) in enumerate(NOTES, start=1):
    c = ws.cell(row=i, column=1, value=text)
    if kind == 'title':
        c.font = Font(name=F, size=14, bold=True, color='1F4E79')
    elif kind == 'h2':
        c.font = Font(name=F, size=11, bold=True, color='C00000')
        c.fill = WARN
    else:
        c.font = Font(name=F, size=10)
    c.alignment = Alignment(wrap_text=True, vertical='top')
ws.column_dimensions['A'].width = 118
for i in range(1, len(NOTES) + 1):
    ws.row_dimensions[i].height = 30 if len(NOTES[i - 1][0]) > 100 else 15
ws.sheet_view.showGridLines = False

# ---------------------------------------------------------------- 2. raw data
ws = wb.create_sheet('Số liệu')
COLS = [('pham_vi', 'Phạm vi', 12), ('mat', 'Mặt', 7), ('ky', 'Kỳ', 22), ('nam', 'Năm', 7),
        ('thang', 'Tháng', 7), ('gia_tri_goc', 'Giá trị gốc', 12),
        ('dvt_nguon', 'ĐVT nguồn', 15), ('vnd', 'VND', 20), ('nghin_ty', 'Nghìn tỷ', 11),
        ('nguon', 'Nguồn (URL)', 60)]
for j, (_, title, w) in enumerate(COLS, start=1):
    c = ws.cell(row=1, column=j, value=title)
    c.font = Font(name=F, size=10, bold=True, color='FFFFFF')
    c.fill = HDR
    c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.column_dimensions[get_column_letter(j)].width = w
for i, r in enumerate(rows, start=2):
    for j, (key, _, _) in enumerate(COLS, start=1):
        v = r[key]
        if key in ('nam', 'thang', 'vnd'):
            v = int(v) if v not in ('', 'None') else None
        elif key == 'nghin_ty':
            v = float(v) if v not in ('', 'None') else None
        c = ws.cell(row=i, column=j, value=v)
        c.font = Font(name=F, size=10)
        c.border = THIN
        if key == 'vnd':
            c.number_format = '#,##0'
        elif key == 'nghin_ty':
            c.number_format = '#,##0.0'
ws.freeze_panes = 'A2'
ws.auto_filter.ref = f'A1:{get_column_letter(len(COLS))}{len(rows) + 1}'

# ---------------------------------------------------------------- 3. the readable table
ws = wb.create_sheet('Theo tháng', 1)
ws['A1'] = 'THU / CHI NGÂN SÁCH NHÀ NƯỚC CẢ NƯỚC THEO THÁNG — nghìn tỷ đồng'
ws['A1'].font = Font(name=F, size=13, bold=True, color='1F4E79')
ws['A2'] = ('Cột "công bố" là số nguồn công bố trong tháng đó. Cột "suy từ luỹ kế" là hiệu hai '
            'mốc luỹ kế liên tiếp. Xem sheet "Đọc trước" trước khi dùng.')
ws['A2'].font = Font(name=F, size=9, italic=True, color='808080')
HEAD = ['Năm', 'Tháng', 'THU công bố', 'THU suy từ luỹ kế', 'CHI công bố',
        'CHI suy từ luỹ kế', 'THU−CHI (công bố)']
for j, t in enumerate(HEAD, start=1):
    c = ws.cell(row=4, column=j, value=t)
    c.font = Font(name=F, size=10, bold=True, color='FFFFFF')
    c.fill = HDR
    c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
ws.column_dimensions['A'].width = 8
ws.column_dimensions['B'].width = 8
for col in 'CDEFG':
    ws.column_dimensions[col].width = 17

# Values, not formulas. The usual rule is the opposite - a sheet should recalculate from its
# inputs - but this workbook is a static extract with no inputs to change: every figure is a
# lookup into the data sheet beside it, and there is no assumption anyone would edit. The
# deciding factor is verification: LibreOffice cannot recalculate in this container (it timed
# out at 539s), so a formula-driven sheet could not be checked before being sent, and shipping
# unverified formulas to a third party is worse than shipping checked values.
#
# A month the source never published is left BLANK, never 0 - SUMIFS would have returned 0 and
# made an unpublished month look like a published zero, which is the one thing this project
# must never do.
idx = {}
for r in rows:
    if r['ky'] in ('tháng', 'tháng (suy từ luỹ kế)') and r['nghin_ty'] not in ('', 'None'):
        idx[(int(r['nam']), int(r['thang']), r['mat'], r['ky'])] = float(r['nghin_ty'])
periods = sorted({(y, m) for (y, m, _, _) in idx})
SERIES = [('thu', 'tháng'), ('thu', 'tháng (suy từ luỹ kế)'),
          ('chi', 'tháng'), ('chi', 'tháng (suy từ luỹ kế)')]
for i, (y, mo) in enumerate(periods, start=5):
    ws.cell(row=i, column=1, value=y).number_format = '0'
    ws.cell(row=i, column=2, value=mo).number_format = '0'
    vals = [idx.get((y, mo, mat, ky)) for mat, ky in SERIES]
    for j, v in enumerate(vals, start=3):
        ws.cell(row=i, column=j, value=v)
    thu_pub, chi_pub = vals[0], vals[2]
    # Rounded to the source's own precision: it publishes one decimal, so 275.9 - 134.4 must
    # read 141.5, not 141.50000000000003.
    ws.cell(row=i, column=7,
            value=round(thu_pub - chi_pub, 1) if None not in (thu_pub, chi_pub) else None)
    for j in range(1, 8):
        c = ws.cell(row=i, column=j)
        c.font = Font(name=F, size=10)
        c.border = THIN
        if j >= 3:
            c.number_format = '#,##0.0;-#,##0.0;-'
ws.freeze_panes = 'A5'
ws.sheet_view.showGridLines = False

wb.save(OUT)
print(f'{OUT}  ·  {len(rows)} dòng dữ liệu, {len(periods)} tháng')

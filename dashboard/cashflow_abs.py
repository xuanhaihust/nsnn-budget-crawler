#!/usr/bin/env python3
"""Absolute VND budget flows - the levels, not the shares.

Two tables, both in VND, both after the magnitude filter the MCP server applies:

  ANNUAL  total revenue and spending per year, summed across the provinces that published,
          with the province count on every row. This is the LEVEL.
  QUARTER quarterly flows differenced from the cumulative B60/B61 points, on a balanced
          panel (only provinces publishing all four points that year). This is the TIMING.

Sums are across PROVINCES, never down an outline - TỔNG THU/TỔNG CHI are published headline
cells, so adding 34 disjoint provincial totals does not double count the way summing a form's
rows does.

Every figure is a lower bound on the 34-province total: provinces that published nothing that
year contribute nothing. Coverage is printed on every row; read it before using the number.
"""
import sqlite3, collections, sys

DB = 'data/nsnn.db'
TY = 1e12                                    # 1 nghìn tỷ VND

#: Ceiling for a PROVINCE-LEVEL headline total. The largest real one in the corpus is Hà Nội
#: 2021, 196.4 nghìn tỷ (raw 196366387 triệu đồng), so anything above 200 nghìn tỷ cannot be a
#: provincial budget total. 9,008 rows (0.45% of the 1,993,578 valued rows, across 24 provinces)
#: sit above it, and they are all the same defect: the source cell is already written in đồng
#: while the table declares "Triệu đồng", so the conversion multiplies by 1e6 a second time -
#: Cần Thơ 2017 publishes 11205000000000 and the warehouse stores 1.12e19 VND.
#: NOT a correction: the published figure stands in the workbook and the warehouse. This only
#: excludes it from a SUM, because one such cell is 10^5 times the total it lands in.
CEILING = 2.0e14
CUM = {'Quý': 0, '6 tháng': 1, '9 tháng': 2, 'Năm': 3}

#: Highest-coverage published headline total for each side, settled and planned.
ANNUAL = [
    # Quyết toán chốt chậm ~18 tháng theo luật, nên chuỗi này dừng ở 2022 - không phải thiếu
    # dữ liệu, mà là quyết toán 2023 chưa tới hạn công bố.
    ('THU  quyết toán',    'B63', 'A TỔNG THU CÂN ĐỐI NSNN',    'QUYẾT TOÁN/TỔNG THU NSNN'),
    ('CHI  quyết toán',    'B62', 'B TỔNG CHI NSĐP',            'QUYẾT TOÁN'),
    # Dự toán và ước thực hiện chạy tới 2024 - đây là chuỗi dùng cho năm gần đây.
    ('THU  ước thực hiện', 'B60', 'A TỔNG THU NSNN TRÊN ĐỊA BÀN', 'ƯỚC THỰC HIỆN QUÝ'),
    ('CHI  ước thực hiện', 'B61', 'TỔNG CHI NSĐP',              'ƯỚC THỰC HIỆN QUÝ'),
    ('THU  dự toán',       'B48', 'TỔNG THU NGÂN SÁCH NHÀ NƯỚC', 'DỰ TOÁN/TỔNG THU NSNN'),
    ('CHI  dự toán',       'B46', 'B TỔNG CHI NSĐP',            'DỰ TOÁN'),
]
QUARTERLY = [
    ('THU NSNN trên địa bàn', 'B60', 'A TỔNG THU NSNN TRÊN ĐỊA BÀN'),
    ('CHI NSĐP',              'B61', 'TỔNG CHI NSĐP'),
]

con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
con.row_factory = sqlite3.Row


def cell(form, ind, series=None, kind=None):
    """Every (province, year) value of one published cell."""
    sql = ["""SELECT p.name prov, pe.year y, pe.kind k, v.vnd vnd
              FROM v_fact v JOIN fact_row f ON f.rowid = v.id
              JOIN dim_province p ON p.id = f.province_id
              JOIN dim_period pe ON pe.id = f.period_id
              WHERE v.form_code = ? AND v.indicator_raw = ? AND v.vnd IS NOT NULL"""]
    a = [form, ind]
    if series: sql.append('AND v.series = ?');  a.append(series)
    if kind:   sql.append('AND pe.kind = ?');   a.append(kind)
    return con.execute(' '.join(sql), a).fetchall()


def magnitude_drop(rows):
    """Drop values 100x or further from that province's own median for the same cell.

    Same rule as mcp_server.tools.magnitude_flag, and the same reason: 757 published values
    sit 100x+ from their own history (a thousands separator read as a decimal point is the
    usual shape). They are NOT corrected - the source figure stands - they are excluded from
    a SUM, because one such cell moves a provincial total by three orders of magnitude.
    Needs >= 3 observations for a province before it can judge one.
    """
    hist = collections.defaultdict(list)
    for r in rows:
        if 0 < r['vnd'] <= CEILING: hist[r['prov']].append(r['vnd'])
    med = {p: sorted(v)[len(v) // 2] for p, v in hist.items() if len(v) >= 3}
    keep, dropped = [], []
    for r in rows:
        m = med.get(r['prov'])
        if r['vnd'] > CEILING:                       # impossible for a provincial total
            dropped.append(r)
        elif m and m > 0 and (r['vnd'] / m < 0.01 or r['vnd'] / m > 100):
            dropped.append(r)                        # far from this cell's own history
        else:
            keep.append(r)
    return keep, dropped


def annual():
    print('\n' + '=' * 96)
    print('TỔNG THU / TỔNG CHI NGÂN SÁCH ĐỊA PHƯƠNG THEO NĂM — SỐ TUYỆT ĐỐI, nghìn tỷ VND')
    print('=' * 96)
    for name, form, ind, series in ANNUAL:
        rows = cell(form, ind, series, kind='Năm')
        keep, dropped = magnitude_drop(rows)
        by = collections.defaultdict(dict)
        for r in keep: by[r['y']][r['prov']] = r['vnd']
        nd = collections.Counter(r['y'] for r in dropped)
        print(f"\n{name}   ({form} · {series})")
        print(f"  {'Năm':<7}{'TỔNG':>16}{'tỉnh':>7}{'/34':<6}{'lớn nhất':>13}{'nhỏ nhất':>12}"
              f"{'loại vì sai độ lớn':>21}")
        for y in sorted(by):
            v = by[y]
            if not v: continue
            print(f"  {y:<7}{sum(v.values())/TY:>16,.1f}{len(v):>7}{'/34':<6}"
                  f"{max(v.values())/TY:>13,.1f}{min(v.values())/TY:>12,.3f}{nd[y]:>21}")


def quarterly():
    print('\n' + '=' * 96)
    print('DÒNG TIỀN TỪNG QUÝ — SỐ TUYỆT ĐỐI, nghìn tỷ VND  (rổ tỉnh cố định trong mỗi năm)')
    print('B60/B61 công bố LUỸ KẾ ở 4 mốc (Quý I · 6 tháng · 9 tháng · Năm); quý = hiệu các mốc.')
    print('=' * 96)
    for name, form, ind in QUARTERLY:
        rows = [r for r in cell(form, ind, series='ƯỚC THỰC HIỆN QUÝ')]
        keep, dropped = magnitude_drop(rows)
        cum = collections.defaultdict(dict)
        for r in keep: cum[(r['prov'], r['y'])][CUM[r['k']]] = r['vnd']
        panel = collections.defaultdict(list)
        miss, neg, zero = collections.Counter(), collections.Counter(), collections.Counter()
        for (prov, y), v in cum.items():
            if len(v) < 4 or v[3] <= 0: miss[y] += 1; continue
            q = [v[0], v[1] - v[0], v[2] - v[1], v[3] - v[2]]
            # Luỹ kế GIẢM (quý âm) và luỹ kế ĐỨNG YÊN (quý = 0) là cùng một loại lỗi nguồn:
            # mốc sau chép lại mốc trước. Đắk Lắk 2019 chi: 16,17 / 0 / 0 / 0,45 - ba phần tư
            # năm dồn vào quý I. Một quý bằng 0 của cả ngân sách tỉnh là không thể có thật, nên
            # để trong bảng gộp sẽ bịa ra tính mùa vụ. Loại khỏi bảng, GIỮ trong CSV kèm cờ.
            if min(q) < 0: neg[y] += 1; continue
            if min(q) == 0: zero[y] += 1; continue
            panel[y].append((prov, q, v[3]))
        print(f"\n{name}   ({form})")
        print(f"  {'Năm':<7}{'Quý I':>13}{'Quý II':>13}{'Quý III':>13}{'Quý IV':>13}{'CẢ NĂM':>14}"
              f"{'tỉnh':>7}{'/34':<5}  loại (thiếu+âm+bằng0)")
        tot = [0.0] * 4
        for y in sorted(panel):
            p = panel[y]
            if not p: continue
            s = [sum(r[1][i] for r in p) for i in range(4)]
            print(f"  {y:<7}" + ''.join(f"{x/TY:>13,.1f}" for x in s)
                  + f"{sum(s)/TY:>14,.1f}{len(p):>7}{'/34':<5}  {miss[y]}+{neg[y]}+{zero[y]}")
            for i in range(4): tot[i] += s[i]
        print(f"  {'GỘP':<7}" + ''.join(f"{x/TY:>13,.1f}" for x in tot) + f"{sum(tot)/TY:>14,.1f}")


def detail(form, ind, series, year, kind='Năm'):
    """Every province behind one cell of one year, so a total can be checked by hand."""
    rows = [r for r in cell(form, ind, series, kind) if r['y'] == year]
    keep, dropped = magnitude_drop(cell(form, ind, series, kind))
    kept = {(r['prov'], r['y']) for r in keep}
    print(f"\n{ind} · {series} · {year} — từng tỉnh, nghìn tỷ VND")
    for r in sorted(rows, key=lambda r: -r['vnd']):
        mark = '' if (r['prov'], r['y']) in kept else '   ← LOẠI: sai độ lớn'
        print(f"  {r['prov']:<22}{r['vnd']/TY:>14,.3f}{mark}")


def csv_out(path='output/ngan_sach_tuyet_doi.csv'):
    """One tidy CSV: every province-year-period figure that survives the filters, in VND."""
    import csv
    n = 0
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['mat', 'bieu', 'series', 'tinh', 'nam', 'ky', 'vnd', 'nghin_ty', 'canh_bao'])
        for name, form, ind, series in ANNUAL:
            keep, _ = magnitude_drop(cell(form, ind, series, kind='Năm'))
            for r in keep:
                w.writerow([name, form, series, r['prov'], r['y'], 'Năm', int(r['vnd']),
                            round(r['vnd'] / TY, 4), '']); n += 1
        for name, form, ind in QUARTERLY:
            keep, _ = magnitude_drop(cell(form, ind, series='ƯỚC THỰC HIỆN QUÝ'))
            cum = collections.defaultdict(dict)
            for r in keep: cum[(r['prov'], r['y'])][CUM[r['k']]] = r['vnd']
            for (prov, y), v in sorted(cum.items()):
                if len(v) < 4 or v[3] <= 0: continue
                q = [v[0], v[1] - v[0], v[2] - v[1], v[3] - v[2]]
                flag = ('luỹ kế giảm - lỗi nguồn' if min(q) < 0 else
                        'luỹ kế đứng yên - lỗi nguồn' if min(q) == 0 else '')
                for i, x in enumerate(q):
                    w.writerow([name, form, 'ƯỚC THỰC HIỆN QUÝ (dòng tiền quý)', prov, y,
                                f'Quý {i+1}', int(x), round(x / TY, 4), flag]); n += 1
    print(f'{path}  {n:,} dòng')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'csv':
        csv_out()
    elif len(sys.argv) > 1 and sys.argv[1] == 'detail':
        detail('B62', 'B TỔNG CHI NSĐP', 'QUYẾT TOÁN', int(sys.argv[2]))
    else:
        annual(); quarterly()
        print('\nMọi con số là TỔNG CỦA CÁC TỈNH ĐÃ CÔNG BỐ, nên là CẬN DƯỚI của tổng 34 tỉnh —')
        print('tỉnh không công bố năm đó đóng góp 0. Và đây chỉ là ngân sách ĐỊA PHƯƠNG.')

import pandas as pd
import numpy as np
import re
import json

# ==========================================
# 1. СБОР И ОЧИСТКА ДАННЫХ ИЗ EXCEL
# ==========================================
files = {
    'Газпромнефть': 'gazpromneft-reviews-full.xlsx',
    'Teboil': 'teboil-reviews-full.xlsx',
    'Роснефть': 'rosneft-reviews-full.xlsx',
    'Татнефть': 'tatneft-reviews-full.xlsx',
    'Лукойл': 'luukoil-reviews-full.xlsx',
    'Нефтьмагистраль': 'neftemag-reviews-full.xlsx'
}

print("1. Чтение Excel файлов...")
dfs = []
for brand, fname in files.items():
    try:
        d = pd.read_excel(fname)
        d['brand'] = brand
        dfs.append(d)
        print(f"   — Загружен: {fname}")
    except Exception as e:
        print(f"   — Ошибка при чтении {fname}: {e}")

if not dfs:
    raise ValueError("Не удалось загрузить ни одного файла!")

m = pd.concat(dfs, ignore_index=True)

print("2. Обработка координат...")
def parse_coords(val):
    if pd.isna(val): return np.nan, np.nan
    p = str(val).strip().split(',')
    if len(p) == 2:
        try:
            p1, p2 = float(p[0]), float(p[1])
            return (p1, p2) if 54 <= p1 <= 57 and 35 <= p2 <= 39 else (p2, p1)
        except: return np.nan, np.nan
    return np.nan, np.nan

coords = m['coords'].apply(parse_coords)
m['latitude'], m['longitude'] = [c[0] for c in coords], [c[1] for c in coords]

print("3. Фильтрация и парсинг дат...")
def parse_date(d):
    if pd.isna(d): return None
    s = str(d).lower().strip()
    months = {'мая':'05','июня':'06','июля':'07','май':'05','июн':'06','июл':'07'}
    y_match = re.search(r'\b(202[0-9])\b', s)
    y = y_match.group(1) if y_match else '2026'
    for mn, num in months.items():
        if mn in s:
            day = next((int(x) for x in s.split() if x.isdigit() and int(x) <= 31 and x != y), None)
            if day: return f"{y}-{num}-{day:02d}"
    return None

m['dt'] = pd.to_datetime(m['review_date'].apply(parse_date), errors='coerce')
df = m[(m['dt'] >= '2026-05-01') & (m['dt'] <= '2026-07-31') & (m['latitude'].notna()) & (m['longitude'].notna())].copy()

if 'address' not in df.columns:
    df['address'] = 'Москва и область'

# ==========================================
# 2. КАТЕГОРИЗАЦИЯ НЕГАТИВА (С УЧЕТОМ ОТРИЦАНИЙ)
# ==========================================
print("4. Категоризация отзывов...")
txt = df['review_text'].astype(str)

# 1. Ограничения и лимиты (исключаем "нет ограничений", "без лимитов", "отменили")
limits_raw = txt.str.contains(r'ограничен|лимит|не более|не больше|литр|по талон|только по|канистр|10 л|20 л|30 л|на руки|в руки', case=False, regex=True)
limits_neg = txt.str.contains(r'нет ограничен|нет лимит|без лимит|без огранич|отменил|сняли ограничен|сняли лимит', case=False, regex=True)
df['is_limits'] = (limits_raw & ~limits_neg).astype(int)

# 2. Закрытие АЗС (исключаем сервисы и лимиты)
closed_raw = txt.str.contains(r'закрыт|не работает|ремонт|переучет|шлагбаум|техперерыв', case=False, regex=True)
service_only = txt.str.contains(r'туалет|кофе|касс|терминал|подкач|колес|приложение|мойк', case=False, regex=True)
df['is_closed'] = (closed_raw & ~service_only & (df['is_limits'] == 0)).astype(int)

# 3. Дефицит / отсутствие топлива
no_fuel_raw = txt.str.contains(r'нет бензин|нет 9|нет дизель|нет дт|закончился|нет 95|нет 92|сухо', case=False, regex=True)
no_fuel_pos = txt.str.contains(r'бензин есть|в наличии|все есть|топливо есть', case=False, regex=True)
df['is_no_fuel'] = (no_fuel_raw & ~no_fuel_pos & (df['is_limits'] == 0)).astype(int)

# 4. Скачок цен
price_negative = txt.str.contains(r'дорог[оаи]|подорож|подняли|завышен|конск|оверпрайс|высокая цена|цены кусаются', case=False, regex=True)
price_positive = txt.str.contains(r'дешевле|скидк|акци|дешевая|выгодно|хорошая цена|адекватн|приемлем|ниже чем|дешево', case=False, regex=True)
df['is_price_hike'] = (price_negative & ~price_positive).astype(int)

# 5. Проблемы с качеством
df['is_bad_fuel'] = txt.str.contains(r'плохой|разбавлен|вода|бодяга|чек горит|чихает|плохое качество', case=False, regex=True).astype(int)

# 6. Очереди и заторы
queue_raw = txt.str.contains(r'очеред|затор|пробк|медленно|колонн', case=False, regex=True)
queue_neg = txt.str.contains(r'нет очеред|без очеред|очереди нет|очередей нет|быстро|без заторов|колонк', case=False, regex=True)
df['is_queue'] = (queue_raw & ~queue_neg).astype(int)

# 7. Прочие отзывы
df['is_other'] = ((df['is_limits'] == 0) &
                  (df['is_closed'] == 0) & 
                  (df['is_no_fuel'] == 0) & 
                  (df['is_price_hike'] == 0) & 
                  (df['is_bad_fuel'] == 0) &
                  (df['is_queue'] == 0)).astype(int)

# ==========================================
# 3. ПОДГОТОВКА ТОЧЕК И ВРЕМЕННОГО ОКНА (СИНХРОНИЗАЦИЯ СИГНАЛА)
# ==========================================
print("5. Расчет динамических весов и синхронизация сигналов...")
start_date = pd.to_datetime('2026-05-01')
end_date = pd.to_datetime('2026-07-31')
total_days = (end_date - start_date).days

features = []
brand_stats = {b: {} for b in files.keys()}

for (lat, lon), group in df.groupby(['latitude', 'longitude']):
    brand = group['brand'].iloc[0]
    group_sorted = group.sort_values(by='dt', ascending=True)
    timeline_properties = {}
    
    for step in range(0, 101, 10):
        current_date = start_date + pd.Timedelta(days=int(total_days * (step / 100)))
        window_start = current_date - pd.Timedelta(days=5)
        
        # Берем отзывы СТРОГО за текущее 5-дневное окно
        w_df = group_sorted[(group_sorted['dt'] >= window_start) & (group_sorted['dt'] <= current_date)]
        
        w_count = len(w_df)
        log_weight = np.log1p(w_count)
        
        counts = {
            'limits': w_df['is_limits'].sum(),
            'closed': w_df['is_closed'].sum(),
            'no_fuel': w_df['is_no_fuel'].sum(),
            'price': w_df['is_price_hike'].sum(),
            'bad_fuel': w_df['is_bad_fuel'].sum(),
            'queue': w_df['is_queue'].sum()
        }
        max_cat = max(counts, key=counts.get)
        status = "ok" if counts[max_cat] == 0 else max_cat
        
        # Сигнал формируется СТРОГО из отзывов текущего окна
        last_comment = "Обстановка спокойная, свежих жалоб нет"
        address_val = str(group.iloc[0].get('address', 'Москва и область'))
        
        if len(w_df) > 0:
            last_row = w_df.iloc[-1]
            last_comment = str(last_row.get('review_text', ''))
            if pd.notna(last_row.get('address')):
                address_val = str(last_row['address'])

        if len(last_comment) > 110:
            last_comment = last_comment[:107] + "..."

        timeline_properties[f"step_{step}_status"] = status
        timeline_properties[f"step_{step}_count"] = w_count
        timeline_properties[f"step_{step}_weight"] = float(log_weight)
        timeline_properties[f"step_{step}_last_comment"] = last_comment
        timeline_properties[f"step_{step}_address"] = address_val

    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "brand": brand,
            "has_real_data": True,
            **timeline_properties
        }
    })

# ==========================================
# 4. РАСЧЕТ СТАТИСТИКИ ПО БРЕНДАМ
# ==========================================
print("6. Генерация общей статистики...")
for step in range(0, 101, 10):
    current_date = start_date + pd.Timedelta(days=int(total_days * (step / 100)))
    window_start = current_date - pd.Timedelta(days=5)
    
    for brand_name in files.keys():
        b_df = df[(df['brand'] == brand_name) & (df['dt'] >= window_start) & (df['dt'] <= current_date)]
        
        brand_stats[brand_name][f"step_{step}"] = {
            "limits": int(b_df['is_limits'].sum()),
            "closed": int(b_df['is_closed'].sum()),
            "no_fuel": int(b_df['is_no_fuel'].sum()),
            "price": int(b_df['is_price_hike'].sum()),
            "bad_fuel": int(b_df['is_bad_fuel'].sum()),
            "queue": int(b_df['is_queue'].sum()),
            "other": int(b_df['is_other'].sum()),
            "total_issues": len(b_df)
        }

with open('h3_aggregated.geojson', 'w', encoding='utf-8') as f:
    json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)

with open('stats.json', 'w', encoding='utf-8') as f:
    json.dump(brand_stats, f, ensure_ascii=False, indent=2)

print("\n----------------------------------------------------")
print("УСПЕШНО! Данные синхронизированы по 5-дневному окну.")
print("----------------------------------------------------")
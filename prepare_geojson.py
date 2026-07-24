import pandas as pd
import json
import h3
import math

print("Загрузка данных...")
df = pd.read_csv("master_azs_may_july_2026.csv")

lon_col = next((c for c in df.columns if c.lower() in ['lon', 'longitude', 'lng', 'coord_lon']), None)
lat_col = next((c for c in df.columns if c.lower() in ['lat', 'latitude', 'coord_lat']), None)
date_col = next((c for c in df.columns if c.lower() in ['dt', 'date', 'datetime', 'created_at']), None)
brand_col = next((c for c in df.columns if c.lower() in ['brand', 'company', 'name']), None)

df = df.dropna(subset=[lon_col, lat_col]).copy()

# 1. GeoJSON отдельных станций
stations_features = []
for idx, row in df.iterrows():
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(row[lon_col]), float(row[lat_col])]
        },
        "properties": {
            "brand": str(row[brand_col]) if brand_col else "Unknown",
            "address": str(row.get("address", "")),
            "is_no_fuel": int(row.get("is_no_fuel", 0)),
            "is_closed": int(row.get("is_closed", 0)),
            "is_price_hike": int(row.get("is_price_hike", 0)),
            "date": str(row[date_col]) if date_col else ""
        }
    }
    stations_features.append(feature)

with open("stations.geojson", "w", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": stations_features}, f, ensure_ascii=False)

# Универсальные функции H3
def get_h3_index(lat, lon, res=8):
    if hasattr(h3, 'latlng_to_cell'):
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)

def get_h3_boundary(cell):
    if hasattr(h3, 'cell_to_boundary'):
        boundary = h3.cell_to_boundary(cell)
        return [[lon, lat] for lat, lon in boundary]
    return h3.h3_to_geo_boundary(cell, geo_json=True)

df["h3_index"] = df.apply(lambda r: get_h3_index(float(r[lat_col]), float(r[lon_col]), res=8), axis=1)

# 2. Агрегация по H3 (ОБЩАЯ + ПО БРЕНДАМ)
h3_features = []

# А) Общая агрегация ("ALL")
grouped_all = df.groupby("h3_index").agg(
    count_total=("is_no_fuel", "count"),
    count_no_fuel=("is_no_fuel", "sum"),
    count_closed=("is_closed", "sum"),
    count_price=("is_price_hike", "sum")
).reset_index()

for idx, row in grouped_all.iterrows():
    h3_idx = row["h3_index"]
    boundary = get_h3_boundary(h3_idx)
    height = round(math.sqrt(row["count_total"]) * 120, 1)

    h3_features.append({
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [boundary]},
        "properties": {
            "brand": "ALL",
            "h3_index": str(h3_idx),
            "count_total": int(row["count_total"]),
            "count_no_fuel": int(row["count_no_fuel"]),
            "count_closed": int(row["count_closed"]),
            "count_price": int(row["count_price"]),
            "height": height
        }
    })

# Б) Агрегация в разрезе каждого бренда
if brand_col:
    grouped_brand = df.groupby(["h3_index", brand_col]).agg(
        count_total=("is_no_fuel", "count"),
        count_no_fuel=("is_no_fuel", "sum"),
        count_closed=("is_closed", "sum"),
        count_price=("is_price_hike", "sum")
    ).reset_index()

    for idx, row in grouped_brand.iterrows():
        h3_idx = row["h3_index"]
        boundary = get_h3_boundary(h3_idx)
        height = round(math.sqrt(row["count_total"]) * 120, 1)

        h3_features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [boundary]},
            "properties": {
                "brand": str(row[brand_col]),
                "h3_index": str(h3_idx),
                "count_total": int(row["count_total"]),
                "count_no_fuel": int(row["count_no_fuel"]),
                "count_closed": int(row["count_closed"]),
                "count_price": int(row["count_price"]),
                "height": height
            }
        })

with open("h3_aggregated.geojson", "w", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": h3_features}, f, ensure_ascii=False)

# 3. Статистика stats.json
stats = {
    "total_records": len(df),
    "total_no_fuel": int(df["is_no_fuel"].sum()) if "is_no_fuel" in df else 0,
    "total_closed": int(df["is_closed"].sum()) if "is_closed" in df else 0,
    "total_price_hike": int(df["is_price_hike"].sum()) if "is_price_hike" in df else 0,
}

if brand_col:
    stats["by_brand"] = df.groupby(brand_col)[["is_no_fuel", "is_closed", "is_price_hike"]].sum().to_dict(orient="index")

with open("stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)

print("--- ОБНОВЛЕННЫЕ GeoJSON И JSON ФАЙЛЫ УСПЕШНО СФОРМИРОВАНЫ! ---")
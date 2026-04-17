"""
Supabase DB 전체 백업 스크립트
- 모든 테이블의 스키마(컬럼 정보)를 JSON으로 저장
- 모든 테이블의 데이터를 CSV + JSON으로 저장
"""
import requests
import json
import csv
import os
import sys

SUPABASE_URL = "https://cefuyzmzhqlnyzysapol.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNlZnV5em16aHFsbnl6eXNhcG9sIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI4NjExOTAsImV4cCI6MjA4ODQzNzE5MH0.2ovB-1pxxXj64PCJFgG3uD96ks401p7QmQOM35tApTE"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

TABLES = [
    "economic_and_stock_data",
    "stock_analysis_results",
    "predicted_stocks",
    "stock_recommendations",
    "ticker_sentiment_analysis",
    "trade_records",
    "llm_decision_logs",
    "access_tokens",
]

BACKUP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BACKUP_DIR, "data")
SCHEMA_DIR = os.path.join(BACKUP_DIR, "schema")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SCHEMA_DIR, exist_ok=True)


def fetch_all_rows(table_name, order_col=None, page_size=1000):
    """페이지네이션으로 테이블의 모든 행을 가져온다."""
    all_rows = []
    offset = 0

    # 첫 요청으로 컬럼 확인 및 정렬 컬럼 결정
    url = f"{SUPABASE_URL}/rest/v1/{table_name}"
    params = {"select": "*", "limit": 1}
    r = requests.get(url, headers={**HEADERS, "Prefer": "count=exact"}, params=params)

    if r.status_code not in (200, 206):
        print(f"  [ERROR] {table_name}: HTTP {r.status_code} - {r.text[:200]}")
        return []

    # 총 행수 확인
    content_range = r.headers.get("content-range", "")
    total = None
    if "/" in content_range:
        total_str = content_range.split("/")[-1]
        if total_str != "*":
            total = int(total_str)

    if total == 0:
        print(f"  {table_name}: 빈 테이블 (0행)")
        return []

    total_str = str(total) if total else "?"
    print(f"  {table_name}: 총 {total_str}행 다운로드 중...", end="", flush=True)

    while True:
        params = {
            "select": "*",
            "offset": offset,
            "limit": page_size,
        }
        if order_col:
            params["order"] = f"{order_col}.asc"

        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code not in (200, 206):
            print(f"\n  [ERROR] offset={offset}: HTTP {r.status_code}")
            break

        rows = r.json()
        if not rows:
            break

        all_rows.extend(rows)
        offset += len(rows)
        print(f"\r  {table_name}: {offset}/{total_str}행 다운로드 중...", end="", flush=True)

        if len(rows) < page_size:
            break

    print(f"\r  {table_name}: {len(all_rows)}행 완료" + " " * 20)
    return all_rows


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def save_csv(rows, filepath):
    if not rows:
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write("")
        return

    keys = list(rows[0].keys())
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def extract_schema(rows, table_name):
    """데이터로부터 컬럼 정보를 추출한다."""
    if not rows:
        return {"table": table_name, "columns": [], "row_count": 0}

    columns = []
    sample = rows[0]
    for key, value in sample.items():
        col_type = type(value).__name__ if value is not None else "unknown"
        # 더 나은 타입 추론
        if isinstance(value, bool):
            col_type = "boolean"
        elif isinstance(value, int):
            col_type = "integer"
        elif isinstance(value, float):
            col_type = "float"
        elif isinstance(value, str):
            col_type = "text"
        elif isinstance(value, list):
            col_type = "array"
        elif isinstance(value, dict):
            col_type = "json"

        # null인 경우 다른 행에서 타입 추론
        if value is None:
            for row in rows[:100]:
                v = row.get(key)
                if v is not None:
                    if isinstance(v, bool):
                        col_type = "boolean"
                    elif isinstance(v, int):
                        col_type = "integer"
                    elif isinstance(v, float):
                        col_type = "float"
                    elif isinstance(v, str):
                        col_type = "text"
                    break

        # 샘플 값 (처음 3개)
        samples = []
        for row in rows[:3]:
            v = row.get(key)
            if v is not None:
                samples.append(str(v)[:100])

        columns.append({
            "name": key,
            "type": col_type,
            "nullable": any(row.get(key) is None for row in rows[:100]),
            "sample_values": samples
        })

    return {
        "table": table_name,
        "columns": columns,
        "column_count": len(columns),
        "row_count": len(rows)
    }


def main():
    print("=" * 60)
    print("Supabase DB 전체 백업 시작")
    print("=" * 60)

    # 테이블별 정렬 컬럼 지정
    order_map = {
        "economic_and_stock_data": "날짜",
        "stock_analysis_results": "id",
        "predicted_stocks": "id",
        "stock_recommendations": "id",
        "ticker_sentiment_analysis": "id",
        "trade_records": "id",
        "llm_decision_logs": "id",
        "access_tokens": "id",
    }

    all_schemas = {}
    summary = []

    for table in TABLES:
        print(f"\n--- {table} ---")
        order_col = order_map.get(table)
        rows = fetch_all_rows(table, order_col=order_col)

        # 데이터 저장
        json_path = os.path.join(DATA_DIR, f"{table}.json")
        csv_path = os.path.join(DATA_DIR, f"{table}.csv")
        save_json(rows, json_path)
        save_csv(rows, csv_path)

        # 스키마 추출 및 저장
        schema = extract_schema(rows, table)
        all_schemas[table] = schema
        schema_path = os.path.join(SCHEMA_DIR, f"{table}_schema.json")
        save_json(schema, schema_path)

        size_json = os.path.getsize(json_path)
        size_csv = os.path.getsize(csv_path) if os.path.exists(csv_path) else 0
        summary.append({
            "table": table,
            "rows": len(rows),
            "columns": schema["column_count"] if rows else 0,
            "json_size_kb": round(size_json / 1024, 1),
            "csv_size_kb": round(size_csv / 1024, 1),
        })

    # 전체 스키마 요약 저장
    save_json(all_schemas, os.path.join(SCHEMA_DIR, "_all_schemas.json"))

    # 요약 출력
    print("\n" + "=" * 60)
    print("백업 완료 요약")
    print("=" * 60)
    print(f"{'테이블':<35} {'행수':>7} {'컬럼':>5} {'JSON':>10} {'CSV':>10}")
    print("-" * 70)
    total_rows = 0
    for s in summary:
        print(f"{s['table']:<35} {s['rows']:>7} {s['columns']:>5} {s['json_size_kb']:>8.1f}KB {s['csv_size_kb']:>8.1f}KB")
        total_rows += s["rows"]
    print("-" * 70)
    print(f"{'총계':<35} {total_rows:>7}")
    print(f"\n저장 위치: {BACKUP_DIR}")
    print(f"  data/    - 테이블 데이터 (JSON + CSV)")
    print(f"  schema/  - 테이블 스키마 정보")


if __name__ == "__main__":
    main()

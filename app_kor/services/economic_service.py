"""
한국 경제/주가 데이터 수집·저장 서비스 (app_kor).

미국판(app/services/economic_service.py) 을 한국 시장용으로 치환:
  - 수집 모듈: stock_kor.collect_economic_data
  - 저장 테이블: economic_and_stock_data_kor
  - 장 시간 가드: 한국 정규장(09:00~15:30 KST) 중에는 수집 연기 (force=True 시 무시)
"""
import pandas as pd
from datetime import datetime, timedelta

from app_kor.db.supabase import supabase
from app_kor.core.config import settings
from app_kor.core.constants import TABLE_ECONOMIC
# stock_kor 는 프로젝트 루트 모듈
from stock_kor import collect_economic_data


def get_last_updated_date():
    """DB 에서 마지막 수집 날짜 조회 → 다음 수집 시작일 반환."""
    try:
        response = supabase.table(TABLE_ECONOMIC).select("날짜").order("날짜", desc=True).limit(1).execute()
        if response.data and len(response.data) > 0:
            last_date = datetime.fromisoformat(response.data[0]["날짜"].replace('Z', '+00:00'))
            next_date = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"마지막 수집 날짜: {last_date.strftime('%Y-%m-%d')}, 다음 수집 시작일: {next_date}")
            return next_date
        print("기존 데이터가 없습니다. 기본 시작 날짜(2010-01-01)로 설정합니다.")
        return "2010-01-01"
    except Exception as e:
        print(f"마지막 수집 날짜 조회 중 오류 발생: {str(e)}")
        return "2010-01-01"


async def update_economic_data_in_background(force: bool = False):
    """백그라운드 경제 데이터 업데이트. force=True 면 장 중 체크 무시."""
    try:
        print("한국 경제 지표 및 주가 데이터 업데이트 작업 시작...")

        now = datetime.now()
        current_hour = now.hour
        current_min = now.minute

        # 한국 정규장(09:00~15:30 KST) 중에는 당일 데이터 미완료 → 수집 연기
        is_market_hours = (
            (current_hour == 9 and current_min >= 0) or
            (9 < current_hour < 15) or
            (current_hour == 15 and current_min <= 30)
        )
        if is_market_hours and not force:
            print(f"현재 시간 {current_hour:02d}:{current_min:02d} 은 한국 장 운영 시간입니다. 장 마감 후 수집합니다.")
            return
        if force and is_market_hours:
            print(f"현재 시간 {current_hour:02d}:{current_min:02d} 은 장 중이지만 강제 수집 모드로 실행합니다.")

        start_date = get_last_updated_date()
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        collection_end_date = today
        storage_end_date = yesterday

        if start_date > storage_end_date:
            print(f"수집 시작일({start_date})이 저장 종료일({storage_end_date})보다 큽니다. 수집할 데이터가 없습니다.")
            return {"success": True, "total_records": 0, "updated_records": 0}

        previous_date = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        prev_data_response = supabase.table(TABLE_ECONOMIC).select("*").eq("날짜", previous_date).execute()
        previous_data = prev_data_response.data[0] if prev_data_response.data else {}

        new_data = collect_economic_data(start_date=start_date, end_date=collection_end_date)
        if new_data is None or new_data.empty:
            print("수집할 새 데이터가 없습니다.")
            return {"success": True, "total_records": 0, "updated_records": 0}

        all_dates = pd.date_range(start=start_date, end=storage_end_date)
        saved_count = 0

        for date in all_dates:
            date_str = date.strftime('%Y-%m-%d')
            if date in new_data.index:
                row = new_data.loc[date]
            else:
                row = pd.Series(dtype='object')

            check = supabase.table(TABLE_ECONOMIC).select("*").eq("날짜", date_str).execute()

            data_dict = {}
            for col_name, value in row.items():
                if not pd.isna(value):
                    data_dict[col_name] = value

            for col_name, value in previous_data.items():
                if col_name not in ("날짜", "id") and col_name not in data_dict and value is not None:
                    data_dict[col_name] = value

            if check.data and len(check.data) > 0:
                existing_data = check.data[0]
                update_dict = {}
                for col_name, value in data_dict.items():
                    if col_name not in existing_data or existing_data[col_name] is None:
                        update_dict[col_name] = value
                if update_dict:
                    supabase.table(TABLE_ECONOMIC).update(update_dict).eq("날짜", date_str).execute()
            else:
                insert_dict = {"날짜": date_str}
                insert_dict.update(data_dict)
                supabase.table(TABLE_ECONOMIC).insert(insert_dict).execute()

            if data_dict:
                previous_data = {"날짜": date_str}
                previous_data.update(data_dict)
            saved_count += 1

        total_records = len(all_dates)
        print(f"총 {total_records}개 날짜 중 {saved_count}개가 처리되었습니다.")
        return {"success": True, "message": "경제 데이터 업데이트 완료", "total_records": total_records, "updated_records": saved_count}
    except Exception as e:
        print(f"경제 데이터 업데이트 중 오류 발생: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise Exception(f"경제 데이터 업데이트 중 오류: {str(e)}")


print(f"Supabase URL: {settings.SUPABASE_URL}")

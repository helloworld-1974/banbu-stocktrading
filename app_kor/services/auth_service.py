def parse_expiration_date(date_str):
    try:
        # 정규 표현식으로 마이크로초 부분 처리
        import re
        if isinstance(date_str, str) and re.search(r'\.\d{5}\+', date_str):  # 5자리 소수점 확인
            # 마이크로초 부분을 6자리로 맞추기
            date_str = re.sub(r'\.(\d{5})\+', r'.\g<1>0+', date_str)

        from datetime import datetime
        import pytz

        if isinstance(date_str, str):
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                return dt
            except ValueError:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    return dt.replace(tzinfo=pytz.UTC)
                except Exception:
                    pass
        # 이미 datetime 객체인 경우
        return date_str
    except Exception as e:
        print(f"날짜 파싱 오류: {e}")
        from datetime import datetime, timedelta
        import pytz
        return datetime.now(pytz.UTC) + timedelta(days=1)

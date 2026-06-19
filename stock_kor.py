"""
한국 시장 경제/주가 데이터 수집 모듈 (app_kor 전용).

미국판 stock.py 를 한국 시장용으로 치환:
  - 거시지표: FRED (글로벌 + 한국 시리즈) — 한국 시장에도 영향 주는 미국 금리/달러 포함
  - 지수/환율: Yahoo Finance (코스피 ^KS11, 코스닥 ^KQ11, 달러/원 KRW=X, VIX ^VIX 등)
  - 개별 종목: Yahoo Finance .KS/.KQ (app_kor.core.constants.STOCK_TO_TICKER)

collect_economic_data() 는 날짜 인덱스 + (지표/종목 한글명 컬럼) DataFrame 을 반환한다.
컬럼명은 economic_and_stock_data_kor 테이블 컬럼과 일치해야 한다.
"""
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

from app_kor.core.config import settings
from app_kor.core.constants import STOCK_TO_TICKER, yahoo_symbol

# FRED API Key
api_key = settings.FRED_API_KEY

# FRED 지표 (글로벌 + 한국). 제공 안 되는 시리즈는 자동 스킵.
fred_indicators = {
    # 글로벌(한국 시장 영향 큰 미국 지표)
    'FEDFUNDS': '미국 기준금리',
    'T10Y2Y': '미국 장단기 금리차',
    'DGS10': '미국 10년 국채금리',
    'DGS2': '미국 2년 국채금리',
    'DTWEXBGS': '달러 인덱스',
    'T10YIE': '미국 10년 기대 인플레이션율',
    # 한국 지표
    'IRLTLT01KRM156N': '한국 장기국채금리',
    'KORCPIALLMINMEI': '한국 소비자물가지수',
    'XTEXVA01KRM667S': '한국 수출',
}

# Yahoo Finance 지수/환율
yfinance_indicators = {
    '코스피': '^KS11',
    '코스닥': '^KQ11',
    '달러/원': 'KRW=X',
    'VIX 지수': '^VIX',
    'S&P 500 지수': '^GSPC',
    '나스닥 종합지수': '^IXIC',
    '닛케이 225': '^N225',
    '상해종합': '000001.SS',
    '항셍': '^HSI',
    '금 가격': 'GC=F',
    '달러 인덱스(DXY)': 'DX-Y.NYB',
    '미국 10년 국채 ETF': 'IEF',
}

result_df = None


def download_yahoo_chart(symbol, start_date, end_date, interval="1d"):
    """Yahoo Finance Chart API 로 종가 시계열 수집."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    delta = end_dt - start_dt

    if delta.days <= 30:
        range_str = "1mo"
    elif delta.days <= 90:
        range_str = "3mo"
    elif delta.days <= 180:
        range_str = "6mo"
    elif delta.days <= 365:
        range_str = "1y"
    elif delta.days <= 730:
        range_str = "2y"
    elif delta.days <= 1825:
        range_str = "5y"
    else:
        range_str = "max"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_str, "interval": interval, "includePrePost": "false", "events": "div|split"}
    r = sess.get(url, params=params)
    r.raise_for_status()
    result = r.json().get("chart", {}).get("result", [None])[0]
    if not result:
        raise ValueError(f"No data for symbol: {symbol}")

    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    date_only = [pd.Timestamp.fromtimestamp(ts).date() for ts in timestamps]
    df = pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(date_only))
    if df.index.duplicated().any():
        df = df[~df.index.duplicated(keep='last')]
    df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
    return df


def collect_economic_data(start_date='2010-01-01', end_date=None):
    """한국 경제/주가 데이터 수집 메인 함수."""
    global result_df
    if end_date is None:
        end_date = datetime.today().strftime('%Y-%m-%d')

    print(f"한국 경제 데이터 수집 시작: {start_date} ~ {end_date}")

    # ── FRED ──
    print("FRED 경제 지표 수집 중...")
    fred_data_frames = []
    for code, name in fred_indicators.items():
        if code in ['FEDFUNDS', 'KORCPIALLMINMEI', 'IRLTLT01KRM156N', 'XTEXVA01KRM667S', 'DTWEXBGS']:
            frequency = 'm'
        else:
            frequency = 'd'
        url = 'https://api.stlouisfed.org/fred/series/observations'
        params = {
            'series_id': code, 'api_key': api_key, 'file_type': 'json',
            'observation_start': start_date, 'observation_end': end_date, 'frequency': frequency,
        }
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                data = response.json().get('observations', [])
                if data:
                    df = pd.DataFrame(data)[['date', 'value']]
                    df.columns = ['date', name]
                    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                    fred_data_frames.append(df.set_index('date'))
                else:
                    print(f"No data for {name} ({code}).")
            else:
                print(f"Failed FRED {name} ({code}): {response.status_code}")
        except Exception as e:
            print(f"FRED 수집 오류 {name} ({code}): {e}")

    for i, df in enumerate(fred_data_frames):
        if df.empty:
            continue
        try:
            fred_data_frames[i] = df.resample('D').ffill()
        except Exception as e:
            print(f"리샘플링 오류 {i}: {e}")

    # ── Yahoo 지수/환율 ──
    print("\nYahoo Finance 지수/환율 수집 중...")
    yfinance_data_frames = []
    for name, ticker in yfinance_indicators.items():
        try:
            df = download_yahoo_chart(ticker, start_date, end_date)
            if not df.empty:
                df.columns = [name]
                df.index = df.index.tz_localize(None)
                yfinance_data_frames.append(df)
                print(f"{name}({ticker}) 수집 완료, {len(df)}개")
        except Exception as e:
            print(f"Yahoo 수집 오류 {ticker} ({name}): {e}")
        time.sleep(1)

    # ── 한국 개별 종목 (.KS/.KQ) ──
    print("\n한국 개별 종목 데이터 수집 중...")
    stock_data_frames = []
    for name, code in STOCK_TO_TICKER.items():
        symbol = yahoo_symbol(code)
        try:
            df = download_yahoo_chart(symbol, start_date, end_date)
            if not df.empty:
                df.columns = [name]
                df.index = df.index.tz_localize(None)
                stock_data_frames.append(df)
                print(f"{name}({symbol}) 수집 완료, {len(df)}개")
        except Exception as e:
            print(f"종목 수집 오류 {symbol} ({name}): {e}")
        time.sleep(1)

    all_data_frames = fred_data_frames + yfinance_data_frames + stock_data_frames
    if not all_data_frames:
        print("수집된 데이터가 없습니다.")
        return None

    for i, df in enumerate(all_data_frames):
        if df.index.duplicated().any():
            all_data_frames[i] = df[~df.index.duplicated(keep='first')]

    print("데이터프레임 병합 중...")
    result_df = pd.concat(all_data_frames, axis=1, join='outer')
    result_df.replace('.', pd.NA, inplace=True)
    result_df.sort_index(inplace=True)
    result_df.ffill(inplace=True)
    result_df.index = pd.to_datetime(result_df.index.date)
    result_df = result_df[~result_df.index.duplicated(keep='last')]

    print(f"\n=== 결과: {len(result_df)}행 x {len(result_df.columns)}열 ===")
    print(f"데이터 수집 완료")
    return result_df


if __name__ == "__main__":
    result_df = collect_economic_data()

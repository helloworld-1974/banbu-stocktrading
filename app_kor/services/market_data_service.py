"""
한국주식 OHLCV/시세 통합 데이터 레이어 (멀티소스).

데이터 소스 3종을 우선순위(settings.KOR_DATA_SOURCE_PRIORITY, 기본 kis→pykrx→yahoo)대로
시도하고, 실패/빈 응답 시 다음 소스로 폴백한다.

모든 소스의 일봉 데이터는 미국판(KIS 해외 dailyprice)과 동일한 형태로 정규화한다:
  output2: [ {xymd, clos, open, high, low, tvol}, ... ]   # index 0 = 최신일 (latest-first)

→ stock_recommendation_service 의 calculate_atr / calculate_adx 가
  미국판과 동일한 키(high/low/clos/tvol/xymd)로 동작 가능.
"""
import logging
from datetime import datetime, timedelta

import requests

from app_kor.core.config import settings
from app_kor.core.constants import KIS_MARKET_DIV_CODE, yahoo_symbol
from app_kor.services.balance_service import _headers

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 1) KIS 국내주식 기간별 일봉 (FHKST03010100)
# ══════════════════════════════════════════════════════════════════

def _kis_daily(ticker: str, count: int, end_date: str) -> list:
    """KIS 국내주식 기간별시세(일봉) 조회 → 정규화 리스트(latest-first) 반환."""
    end_dt = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.now()
    start_dt = end_dt - timedelta(days=max(count * 2, 60))

    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = _headers("FHKST03010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": KIS_MARKET_DIV_CODE,
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",  # 0: 수정주가
    }
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 일봉 조회 실패: {data.get('msg1', '')}")

    rows = []
    for d in data.get("output2", []):
        if not d.get("stck_bsop_date"):
            continue
        rows.append({
            "xymd": d.get("stck_bsop_date", ""),
            "clos": d.get("stck_clpr", "0"),
            "open": d.get("stck_oprc", "0"),
            "high": d.get("stck_hgpr", "0"),
            "low": d.get("stck_lwpr", "0"),
            "tvol": d.get("acml_vol", "0"),
        })
    rows.sort(key=lambda r: r["xymd"], reverse=True)
    return rows


# ══════════════════════════════════════════════════════════════════
# 2) pykrx
# ══════════════════════════════════════════════════════════════════

def _pykrx_daily(ticker: str, count: int, end_date: str) -> list:
    """pykrx 일봉 조회 → 정규화 리스트(latest-first) 반환."""
    from pykrx import stock as pykrx_stock  # 지연 import (선택 의존성)

    end_dt = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.now()
    start_dt = end_dt - timedelta(days=max(count * 2, 60))
    df = pykrx_stock.get_market_ohlcv_by_date(
        start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"), ticker
    )
    if df is None or df.empty:
        raise RuntimeError("pykrx 빈 응답")

    rows = []
    for idx, r in df.iterrows():
        rows.append({
            "xymd": idx.strftime("%Y%m%d"),
            "clos": str(int(r["종가"])),
            "open": str(int(r["시가"])),
            "high": str(int(r["고가"])),
            "low": str(int(r["저가"])),
            "tvol": str(int(r["거래량"])),
        })
    rows.sort(key=lambda x: x["xymd"], reverse=True)
    return rows


# ══════════════════════════════════════════════════════════════════
# 3) Yahoo Finance (.KS/.KQ)
# ══════════════════════════════════════════════════════════════════

def _yahoo_daily(ticker: str, count: int, end_date: str) -> list:
    """Yahoo Finance Chart API 일봉 조회 → 정규화 리스트(latest-first) 반환."""
    symbol = yahoo_symbol(ticker)
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "6mo", "interval": "1d", "includePrePost": "false"}
    r = sess.get(url, params=params)
    r.raise_for_status()
    result = r.json().get("chart", {}).get("result", [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo 빈 응답: {symbol}")

    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(timestamps):
        c = quote["close"][i]
        if c is None:
            continue
        rows.append({
            "xymd": datetime.fromtimestamp(ts).strftime("%Y%m%d"),
            "clos": str(c),
            "open": str(quote["open"][i] if quote["open"][i] is not None else c),
            "high": str(quote["high"][i] if quote["high"][i] is not None else c),
            "low": str(quote["low"][i] if quote["low"][i] is not None else c),
            "tvol": str(int(quote["volume"][i] or 0)),
        })
    rows.sort(key=lambda x: x["xymd"], reverse=True)
    return rows


_SOURCE_FUNCS = {
    "kis": _kis_daily,
    "pykrx": _pykrx_daily,
    "yahoo": _yahoo_daily,
}


# ══════════════════════════════════════════════════════════════════
# 통합 진입점
# ══════════════════════════════════════════════════════════════════

def get_daily_price(ticker: str, count: int = 100, end_date: str = "") -> dict:
    """우선순위대로 소스를 시도해 일봉 데이터를 정규화 형태로 반환.

    Returns:
        {"rt_cd": "0", "source": "kis"|"pykrx"|"yahoo", "output2": [...]}  성공
        {"rt_cd": "1", "msg1": "...", "output2": []}                         전체 실패
    """
    errors = []
    for src in settings.data_source_priority:
        func = _SOURCE_FUNCS.get(src)
        if func is None:
            continue
        try:
            rows = func(ticker, count, end_date)
            if rows:
                return {"rt_cd": "0", "source": src, "output2": rows}
            errors.append(f"{src}: 빈 응답")
        except Exception as e:
            errors.append(f"{src}: {e}")
            logger.warning(f"{ticker} {src} 일봉 조회 실패: {e}")
    return {"rt_cd": "1", "msg1": "; ".join(errors) or "모든 소스 실패", "output2": []}


def get_latest_close(ticker: str) -> float:
    """최신 종가 조회 (멀티소스). 실패 시 0.0."""
    result = get_daily_price(ticker, count=2)
    if result.get("rt_cd") == "0" and result.get("output2"):
        try:
            return float(result["output2"][0].get("clos", 0) or 0)
        except (ValueError, TypeError):
            return 0.0
    return 0.0

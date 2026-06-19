"""
한국주식 거래량 조회 서비스.

미국판(app/services/volume_service.py) 의 해외주식 거래량 로직을 국내주식용으로 치환.
일봉 데이터는 멀티소스 레이어(market_data_service)를 통해 조회한다.
"""
import time
from datetime import datetime

from app_kor.core.constants import STOCK_TO_TICKER
from app_kor.services.balance_service import get_current_price
from app_kor.services import market_data_service

# 한국 주요 종목 (ETF 2개 제외한 본주 유니버스)
KEY_STOCKS = list(STOCK_TO_TICKER.values())[:-2]
# 전체 유니버스 (ETF 포함)
ALL_STOCKS = list(STOCK_TO_TICKER.values())


def get_daily_price(ticker: str, end_date: str = "", count: int = 100) -> dict:
    """국내주식 기간별 일봉 조회 (멀티소스).

    미국판 get_overseas_daily_price 와 동일한 반환 형태:
        {"rt_cd": "0", "output2": [{xymd, clos, open, high, low, tvol}, ...]}  (latest-first)
    """
    return market_data_service.get_daily_price(ticker, count=count, end_date=end_date)


def get_stock_volume_info(ticker: str):
    """개별 종목의 현재가 + 거래량 정보 조회 (KIS inquire-price)."""
    try:
        result = get_current_price(ticker)
        return result
    except Exception as e:
        print(f"현재가 조회 오류 ({ticker}): {str(e)}")
        return None


def get_top_volume_stocks(stock_list=None, top_n=20, delay=0.5):
    """종목 리스트에서 거래량 상위 종목 조회 (KIS 현재가 API)."""
    if stock_list is None:
        stock_list = ALL_STOCKS

    results = []
    errors = []
    ticker_to_name = {v: k for k, v in STOCK_TO_TICKER.items()}

    for i, ticker in enumerate(stock_list):
        try:
            result = get_stock_volume_info(ticker)
            if result and result.get("rt_cd") == "0":
                output = result.get("output", {})
                tvol = output.get("acml_vol", "0")
                tamt = output.get("acml_tr_pbmn", "0")
                last = output.get("stck_prpr", "0")
                rate = output.get("prdy_ctrt", "0")
                results.append({
                    "ticker": ticker,
                    "name": ticker_to_name.get(ticker, ticker),
                    "last_price": float(last) if last and last.strip() else 0.0,
                    "change_rate": float(rate) if rate and rate.strip() else 0.0,
                    "volume": int(tvol) if tvol and tvol.strip() else 0,
                    "trade_amount": float(tamt) if tamt and tamt.strip() else 0.0,
                })
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                errors.append({"ticker": ticker, "error": error_msg})
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})

        if i < len(stock_list) - 1:
            time.sleep(delay)

    results.sort(key=lambda x: x["volume"], reverse=True)
    return {
        "message": f"{len(results)}개 종목 조회 완료 (거래량 상위 {min(top_n, len(results))}개)",
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_queried": len(stock_list),
        "success_count": len(results),
        "error_count": len(errors),
        "top_volumes": results[:top_n],
        "errors": errors if errors else None,
    }


def get_volume_surge_stocks(stock_list=None, days=5, surge_ratio=2.0, delay=0.5):
    """거래량 급등 종목 감지 — 최근 거래량이 N일 평균 대비 surge_ratio배 이상."""
    if stock_list is None:
        stock_list = KEY_STOCKS

    surge_stocks = []
    normal_stocks = []
    errors = []
    ticker_to_name = {v: k for k, v in STOCK_TO_TICKER.items()}

    for i, ticker in enumerate(stock_list):
        try:
            result = get_daily_price(ticker)
            if result and result.get("rt_cd") == "0":
                daily_data = result.get("output2", [])
                if len(daily_data) < days + 1:
                    errors.append({"ticker": ticker, "error": f"데이터 부족 ({len(daily_data)}일)"})
                    continue

                today_vol = int(daily_data[0].get("tvol", "0") or "0")
                today_price = float(daily_data[0].get("clos", "0") or "0")

                past_volumes = [int(d.get("tvol", "0") or "0") for d in daily_data[1:days + 1]]
                past_volumes = [v for v in past_volumes if v > 0]
                if not past_volumes:
                    errors.append({"ticker": ticker, "error": "과거 거래량 데이터 없음"})
                    continue

                avg_vol = sum(past_volumes) / len(past_volumes)
                vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

                stock_info = {
                    "ticker": ticker,
                    "name": ticker_to_name.get(ticker, ticker),
                    "today_volume": today_vol,
                    "avg_volume": int(avg_vol),
                    "volume_ratio": round(vol_ratio, 2),
                    "last_price": today_price,
                }
                if vol_ratio >= surge_ratio:
                    surge_stocks.append(stock_info)
                else:
                    normal_stocks.append(stock_info)
            else:
                error_msg = result.get("msg1", "알 수 없는 오류") if result else "응답 없음"
                errors.append({"ticker": ticker, "error": error_msg})
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})

        if i < len(stock_list) - 1:
            time.sleep(delay)

    surge_stocks.sort(key=lambda x: x["volume_ratio"], reverse=True)
    return {
        "message": f"{len(surge_stocks)}개 거래량 급등 종목 감지 (기준: {days}일 평균 대비 {surge_ratio}배 이상)",
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "surge_ratio_threshold": surge_ratio,
        "avg_days": days,
        "surge_stocks": surge_stocks,
        "errors": errors if errors else None,
    }

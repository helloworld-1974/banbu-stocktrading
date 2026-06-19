from fastapi import APIRouter, HTTPException, Query
from app_kor.services.volume_service import (
    get_top_volume_stocks, get_volume_surge_stocks, get_daily_price,
    KEY_STOCKS, ALL_STOCKS,
)

router = APIRouter()


@router.get("/top", summary="거래량 상위 종목 조회")
def get_top_volume_route(
    scope: str = Query("key", description="조회 범위 (key: 주요 종목, full: 전체 유니버스)"),
    top_n: int = Query(20, description="상위 N개 반환", ge=1, le=100),
):
    """한국주식 종목들의 거래량을 조회하고 상위 종목을 반환합니다."""
    try:
        stock_list = ALL_STOCKS if scope == "full" else KEY_STOCKS
        return get_top_volume_stocks(stock_list=stock_list, top_n=top_n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"거래량 상위 종목 조회 오류: {str(e)}")


@router.get("/surge", summary="거래량 급등 종목 감지")
def get_volume_surge_route(
    scope: str = Query("key", description="조회 범위 (key: 주요 종목, full: 전체 유니버스)"),
    days: int = Query(5, description="평균 거래량 산출 기간 (일)", ge=2, le=30),
    surge_ratio: float = Query(2.0, description="급등 기준 배수 (기본 2.0)", ge=1.0),
):
    """최근 거래량이 N일 평균 대비 급등한 종목을 감지합니다."""
    try:
        stock_list = ALL_STOCKS if scope == "full" else KEY_STOCKS
        return get_volume_surge_stocks(stock_list=stock_list, days=days, surge_ratio=surge_ratio)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"거래량 급등 종목 감지 오류: {str(e)}")


@router.get("/daily/{ticker}", summary="개별 종목 기간별 일봉 조회 (거래량 포함)")
def get_daily_price_route(
    ticker: str,
    bymd: str = Query("", description="조회기준일 (YYYYMMDD, 비우면 최근)"),
):
    """개별 종목의 일봉을 멀티소스(kis/pykrx/yahoo)로 조회합니다."""
    try:
        result = get_daily_price(ticker, end_date=bymd)
        if result.get("rt_cd") != "0":
            raise HTTPException(status_code=400, detail=result.get("msg1", "조회 실패"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"기간별 시세 조회 오류: {str(e)}")

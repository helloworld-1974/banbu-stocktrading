from fastapi import APIRouter, HTTPException
from app_kor.db.supabase import supabase
from app_kor.core.constants import STOCK_TO_TICKER, TICKER_TO_MARKET


router = APIRouter()


@router.get("/universe", summary="한국주식 종목 유니버스 조회")
def read_universe():
    """app_kor 가 다루는 종목 유니버스(한글명 → 종목코드 + 시장)."""
    return {
        "count": len(STOCK_TO_TICKER),
        "stocks": [
            {"name": name, "ticker": code, "market": TICKER_TO_MARKET.get(code, "KOSPI")}
            for name, code in STOCK_TO_TICKER.items()
        ],
    }


@router.get("/{ticker}", summary="특정 종목 정보 조회")
def read_stock_info(ticker: str):
    """stocks_kor 테이블에서 종목 정보를 조회 (없으면 404)."""
    try:
        response = supabase.table("stocks_kor").select("*").eq("symbol", ticker).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail=f"{ticker} 종목 정보를 찾을 수 없습니다.")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"종목 정보 조회 중 오류 발생: {str(e)}")

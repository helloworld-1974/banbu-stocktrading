from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app_kor.services.balance_service import (
    get_domestic_balance, get_all_balances, get_current_price,
    inquire_psbl_amount, order_domestic_stock, get_domestic_nccs,
)

router = APIRouter()


@router.get("/", summary="국내주식 잔고 조회")
def read_balance():
    try:
        return get_domestic_balance()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"잔고 조회 중 오류 발생: {str(e)}")


@router.get("/all", summary="전체 보유 잔고 조회 (정규화)")
def read_all_balances():
    try:
        return get_all_balances()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"잔고 조회 중 오류 발생: {str(e)}")


@router.get("/quotations/price", summary="국내주식 현재가 조회")
def get_current_price_route(ticker: str = Query(..., description="6자리 종목코드 (예: 005930)")):
    """국내주식 현재가 조회. output.stck_prpr 가 현재가(원)."""
    try:
        result = get_current_price(ticker)
        if result.get("rt_cd") != "0":
            raise HTTPException(status_code=400, detail=result.get("msg1", "조회 실패"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"현재가 조회 중 오류 발생: {str(e)}")


@router.get("/inquire-psbl-order", summary="국내주식 매수가능금액 조회")
def inquire_psbl_amount_route(
    ticker: str = Query(..., description="6자리 종목코드"),
    ord_unpr: str = Query(..., description="주문 단가(원)"),
    ord_dvsn: str = Query("00", description="주문구분 (00:지정가, 01:시장가)"),
):
    try:
        result = inquire_psbl_amount(ticker, ord_unpr, ord_dvsn)
        if result.get("rt_cd") != "0":
            raise HTTPException(status_code=400, detail=result.get("msg1", "조회 실패"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매수가능금액 조회 중 오류 발생: {str(e)}")


class OrderRequest(BaseModel):
    pdno: str            # 종목코드 (예: 005930)
    ord_qty: str         # 주문수량
    ord_unpr: str        # 주문단가 (시장가면 "0")
    is_buy: bool = True  # 매수 여부
    ord_dvsn: str = "00"  # 주문구분 (00:지정가, 01:시장가)


@router.post("/order", summary="국내주식 매수/매도 주문")
def order_route(request: OrderRequest):
    """국내주식 현금 매수/매도 주문."""
    try:
        order_data = {
            "PDNO": request.pdno,
            "ORD_QTY": request.ord_qty,
            "ORD_UNPR": request.ord_unpr,
            "ORD_DVSN": request.ord_dvsn,
            "is_buy": request.is_buy,
        }
        result = order_domestic_stock(order_data)
        if result.get("rt_cd") != "0":
            raise HTTPException(status_code=400, detail=result.get("msg1", "주문 처리 실패"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"주문 처리 중 오류 발생: {str(e)}")


@router.get("/nccs", summary="국내주식 미체결(정정취소가능) 내역 조회")
def get_nccs_route():
    """국내주식 정정취소가능주문(미체결) 조회. 모의투자는 미지원."""
    try:
        result = get_domestic_nccs()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"미체결내역 조회 중 오류 발생: {str(e)}")

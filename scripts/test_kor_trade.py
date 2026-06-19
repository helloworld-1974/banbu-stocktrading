"""
한국주식 매수/매도 수동 테스트 스크립트 (모의투자 전용).

balance_service 의 토큰/잔고/시세/매수가능/주문 API 를 순차 호출해
배선(wiring)과 응답을 검증한다. KIS_USE_MOCK=true 일 때만 실행 가능.

  (프로젝트 루트에서 실행)
  venv/bin/python scripts/test_kor_trade.py [종목코드] [수량]
  예) venv/bin/python scripts/test_kor_trade.py 005930 1
"""
import sys
import json
import time

from app_kor.core.config import settings
from app_kor.core.constants import round_to_tick
from app_kor.services import balance_service as bs


def dump(title, obj):
    time.sleep(1.0)  # KIS 모의투자 초당 거래건수 제한 회피
    print(f"\n{'='*60}\n{title}\n{'='*60}")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2)[:2000])
    else:
        print(obj)


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005930"  # 기본: 삼성전자
    qty = sys.argv[2] if len(sys.argv) > 2 else "1"

    print(f"모드: {'모의투자(MOCK)' if settings.KIS_USE_MOCK else '★실거래(REAL)★'}")
    print(f"계좌: {settings.KIS_CANO}-{settings.KIS_ACNT_PRDT_CD}")
    print(f"종목: {ticker}  수량: {qty}")

    if not settings.KIS_USE_MOCK:
        print("\n[중단] 실거래 모드입니다. 테스트는 모의투자(KIS_USE_MOCK=true)에서만 실행하세요.")
        return

    # 1) 토큰
    token = bs.get_access_token()
    dump("1. 액세스 토큰", token[:20] + "..." if token else "발급 실패")

    # 2) 잔고 (주문 전)
    bal = bs.get_all_balances()
    dump("2. 잔고 조회 (주문 전)", {
        "rt_cd": bal.get("rt_cd"), "msg1": bal.get("msg1"),
        "보유종목수": len(bal.get("output1", [])),
        "output2": bal.get("output2"),
    })

    # 3) 현재가
    price_res = bs.get_current_price(ticker)
    cur_price = (price_res.get("output") or {}).get("stck_prpr")
    dump("3. 현재가 조회", {"rt_cd": price_res.get("rt_cd"),
                          "msg1": price_res.get("msg1"),
                          "현재가": cur_price})

    if not cur_price:
        print("\n현재가 조회 실패 — 주문 테스트 중단")
        return

    # 지정가는 현재가 기준 호가단위 정렬
    limit_price = round_to_tick(float(cur_price))

    # 4) 매수가능금액
    psbl = bs.inquire_psbl_amount(ticker, str(limit_price))
    dump("4. 매수가능금액", {"rt_cd": psbl.get("rt_cd"),
                          "msg1": psbl.get("msg1"),
                          "output": psbl.get("output")})

    # 5) 매수 주문 (시장가)
    buy_res = bs.order_domestic_stock({
        "PDNO": ticker,
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
        "ORD_DVSN": "01",  # 시장가
        "is_buy": True,
    })
    dump("5. 매수 주문 (시장가)", buy_res)

    # 6) 매도 주문 (시장가)
    sell_res = bs.order_domestic_stock({
        "PDNO": ticker,
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
        "ORD_DVSN": "01",  # 시장가
        "is_buy": False,
    })
    dump("6. 매도 주문 (시장가)", sell_res)

    # 7) 잔고 (주문 후)
    bal2 = bs.get_all_balances()
    dump("7. 잔고 조회 (주문 후)", {
        "rt_cd": bal2.get("rt_cd"),
        "보유종목수": len(bal2.get("output1", [])),
        "보유종목": [{"종목": h.get("prdt_name"), "수량": h.get("hldg_qty")}
                  for h in bal2.get("output1", [])],
    })

    print("\n테스트 완료.")


if __name__ == "__main__":
    main()

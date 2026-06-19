"""
한국주식 NXT(넥스트레이드) 애프터마켓 실거래 왕복 테스트.

★실제 계좌·실제 돈★ 으로 1주 매수 → 1주 매도 (round-trip) 를 NXT 거래소에서 실행한다.
즉시 체결을 위해 호가(매도1호가에 매수 / 매수1호가에 매도) 기준 지정가 주문.

  (프로젝트 루트에서 실행)
  KIS_USE_MOCK=false venv/bin/python scripts/test_kor_nxt_real.py [종목코드]
  기본 종목: 011200 (HMM)

안전장치: KIS_USE_MOCK=true 면 즉시 중단(실거래 전용).
"""
import sys
import json
import time

import requests

from app_kor.core.config import settings
from app_kor.services import balance_service as bs


def get_nxt_orderbook(ticker: str) -> dict:
    """NXT 호가 조회 (inquire-asking-price-exp-ccn, FHKST01010200, 시장구분 NX)."""
    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    headers = bs._headers("FHKST01010200")
    params = {"FID_COND_MRKT_DIV_CODE": "NX", "FID_INPUT_ISCD": ticker}
    return requests.get(url, headers=headers, params=params).json()


def held_qty(ticker: str) -> int:
    bal = bs.get_all_balances()
    for h in bal.get("output1", []):
        if h.get("pdno") == ticker:
            return int(h.get("hldg_qty", 0) or 0)
    return 0


def dump(title, obj):
    print(f"\n{'='*60}\n{title}\n{'='*60}")
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:1500] if isinstance(obj, (dict, list)) else obj)


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "011200"  # HMM
    qty = "1"

    if settings.KIS_USE_MOCK:
        print("[중단] 모의투자 모드입니다. 이 스크립트는 실거래(KIS_USE_MOCK=false) 전용입니다.")
        return

    print("★" * 30)
    print(f"실거래(REAL) NXT 왕복 테스트  계좌={settings.KIS_CANO}-{settings.KIS_ACNT_PRDT_CD}")
    print(f"종목={ticker}  수량={qty}  거래소=NXT")
    print("★" * 30)

    start_qty = held_qty(ticker)
    print(f"\n[사전] 보유수량: {start_qty}")

    # 1) NXT 호가
    time.sleep(0.7)
    ob = get_nxt_orderbook(ticker)
    out = (ob.get("output1") or ob.get("output") or {}) if isinstance(ob, dict) else {}
    askp1 = int(out.get("askp1", 0) or 0)   # 매도1호가 (여기에 매수 걸면 체결)
    bidp1 = int(out.get("bidp1", 0) or 0)   # 매수1호가 (여기에 매도 걸면 체결)
    print(f"[호가] 매도1호가(askp1)={askp1}  매수1호가(bidp1)={bidp1}  rt_cd={ob.get('rt_cd')}")

    if askp1 <= 0:
        print("NXT 매도호가 없음 — 체결 불가 가능성. 중단.")
        dump("호가 원본", ob)
        return
    if askp1 > 50000:
        print(f"[중단] 매수단가 {askp1}원 > 5만원 한도.")
        return

    # 2) 매수 (NXT 지정가, 매도1호가)
    time.sleep(0.7)
    buy = bs.order_domestic_stock({
        "PDNO": ticker, "ORD_QTY": qty, "ORD_UNPR": str(askp1),
        "ORD_DVSN": "00", "EXCG_ID_DVSN_CD": "NXT", "is_buy": True,
    })
    dump("1. 매수 주문 (NXT 지정가)", buy)

    # 3) 체결 대기 후 보유 확인
    time.sleep(3)
    after_buy_qty = held_qty(ticker)
    print(f"\n[매수 후] 보유수량: {after_buy_qty} (이전 {start_qty})")

    sell_qty = after_buy_qty - start_qty
    if sell_qty <= 0:
        print("매수 미체결 — 잔량이 늘지 않음. 매도 생략. (주문 응답은 위 참조)")
        return

    # 4) 매도 (NXT 지정가, 매수1호가)
    time.sleep(0.7)
    ob2 = get_nxt_orderbook(ticker)
    out2 = (ob2.get("output1") or {}) if isinstance(ob2, dict) else {}
    bidp1 = int(out2.get("bidp1", 0) or 0)
    sell_price = bidp1 if bidp1 > 0 else askp1
    time.sleep(0.7)
    sell = bs.order_domestic_stock({
        "PDNO": ticker, "ORD_QTY": str(sell_qty), "ORD_UNPR": str(sell_price),
        "ORD_DVSN": "00", "EXCG_ID_DVSN_CD": "NXT", "is_buy": False,
    })
    dump(f"2. 매도 주문 (NXT 지정가 {sell_price}, {sell_qty}주)", sell)

    # 5) 최종 확인
    time.sleep(3)
    final_qty = held_qty(ticker)
    print(f"\n[최종] 보유수량: {final_qty} (시작 {start_qty})")
    print("왕복 완료." if final_qty == start_qty else "주의: 시작 수량과 다름 — 매도 미체결 가능.")


if __name__ == "__main__":
    main()

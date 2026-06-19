"""
국내주식 분석 단독 실행 러너 (매수/매도 주문 없음).

정식 매수 파이프라인의 분석 단계만 그대로 실행한다:
    1) 기술적 분석   generate_technical_recommendations()
    2) 뉴스 감성     fetch_and_store_sentiment_for_recommendations()
    3) 통합 후보     get_combined_recommendations_with_technical_and_sentiment()
    4) LLM 최종검토   review_buy_candidates()  (BUY/HOLD 판정만, 실제 주문 X)

사용:
    PYTHONPATH=. venv/bin/python scripts/run_kor_analysis.py
"""
import sys
import time
from app_kor.services.stock_recommendation_service import StockRecommendationService
from app_kor.services.llm_review_service import review_buy_candidates


def _print_technical(data):
    print("\n" + "=" * 110)
    print(f"[1/4] 기술적 분석 — {len(data)}개 종목")
    print("=" * 110)
    if not data:
        print("  결과 없음")
        return
    header = (
        f"{'종목':<14}{'기준일':<12}{'RSI':>7}{'골든':>5}{'MACD신호':>8}"
        f"{'ADX':>7}{'거래량비':>8}{'등락%':>8}{'추천':>6}"
    )
    print(header)
    print("-" * 110)
    data_sorted = sorted(data, key=lambda d: (not d.get("추천_여부"), d.get("RSI", 100)))
    rec = 0
    for d in data_sorted:
        if d.get("추천_여부"):
            rec += 1
        adx, vr, dc = d.get("adx"), d.get("volume_ratio"), d.get("daily_change_pct")
        print(
            f"{d['종목']:<14}{d['날짜']:<12}{d['RSI']:>7.1f}"
            f"{('O' if d.get('골든_크로스') else 'X'):>5}"
            f"{('O' if d.get('MACD_매수_신호') else 'X'):>8}"
            f"{(f'{adx:.1f}' if adx is not None else '-'):>7}"
            f"{(f'{vr:.2f}' if vr is not None else '-'):>8}"
            f"{(f'{dc:+.2f}' if dc is not None else '-'):>8}"
            f"{('★매수' if d.get('추천_여부') else ''):>6}"
        )
    print("-" * 110)
    print(f"  기술적 매수신호(골든크로스+RSI≤65+MACD매수): {rec}개")


def main():
    service = StockRecommendationService()

    # 1) 기술적 분석
    t0 = time.time()
    tech = service.generate_technical_recommendations()
    _print_technical(tech.get("data", []))
    print(f"  소요 {time.time() - t0:.1f}s")

    # 2) 뉴스 감성
    print("\n" + "=" * 110)
    print("[2/4] 뉴스 감성 분석")
    print("=" * 110)
    t0 = time.time()
    sent = service.fetch_and_store_sentiment_for_recommendations()
    print(f"  {sent.get('message', '')}")
    for r in sent.get("results", []):
        score = r.get("average_sentiment_score")
        msg = r.get("message", "")
        print(f"    {r.get('stock_name'):<14} score={score if score is not None else '-'} "
              f"기사={r.get('article_count', 0)} {msg}")
    print(f"  소요 {time.time() - t0:.1f}s")

    # 3) 통합 후보 (ML + 기술 + 감성 + 시장환경)
    print("\n" + "=" * 110)
    print("[3/4] 통합 매수 후보 추출 (ML + 기술 + 감성 + VIX)")
    print("=" * 110)
    t0 = time.time()
    combined = service.get_combined_recommendations_with_technical_and_sentiment()
    candidates = combined.get("results", [])
    print(f"  {combined.get('message', '')}")
    for c in candidates:
        print(f"    {c['stock_name']:<14}({c['ticker']}) "
              f"score={c.get('composite_score', 0):+.4f} "
              f"상승확률={c.get('rise_probability', 0):.2f}% RSI={c.get('rsi', 0):.1f} "
              f"감성={c.get('sentiment_score')}")
    print(f"  소요 {time.time() - t0:.1f}s")

    # 4) LLM 최종 검토
    print("\n" + "=" * 110)
    print("[4/4] LLM 최종 검토 (BUY/HOLD 판정 — 실제 주문 없음)")
    print("=" * 110)
    if not candidates:
        print("  매수 후보가 없어 LLM 검토를 건너뜁니다.")
        print("\n" + "=" * 110)
        print("최종: BUY 0 / HOLD 0 (통합 후보 없음)")
        print("=" * 110)
        return

    t0 = time.time()
    vix_value = candidates[0].get("vix_value")
    review = review_buy_candidates(candidates, vix_value)
    buy = review.get("reviewed_candidates", [])
    hold = review.get("held_candidates", [])
    print(f"  시장 분석: {review.get('llm_reasoning', '')}")
    print(f"\n  ▶ BUY {len(buy)}개")
    for c in buy:
        print(f"    ★ {c['stock_name']:<14}({c['ticker']}) - {c.get('llm_reason', '')}")
    print(f"\n  ▶ HOLD {len(hold)}개")
    for c in hold:
        print(f"    · {c['stock_name']:<14}({c['ticker']}) - {c.get('llm_reason', '')}")
    print(f"  소요 {time.time() - t0:.1f}s")

    print("\n" + "=" * 110)
    print(f"최종: BUY {len(buy)} / HOLD {len(hold)}")
    print("=" * 110)


if __name__ == "__main__":
    sys.exit(main())

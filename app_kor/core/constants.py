"""
한국 시장 전용 상수 모음.

  - 종목 유니버스 (KOSPI/KOSDAQ 대형주 + ETF)
  - DB 테이블명 (_kor 접미사)
  - 한국 시장 시간 (KST)
  - 호가 단위(tick size) 계산 (2023 KRX 호가가격단위 개편 반영)
"""

# ══════════════════════════════════════════════════════════════════
# 종목 유니버스
#   - 미국판 STOCK_TO_TICKER 와 동일하게 "한글명 → 6자리 종목코드" 매핑
#   - 마지막 2개(ETF)는 기술적 추천 대상에서 제외됨 (stock_columns[:-2] 관례)
# ══════════════════════════════════════════════════════════════════

STOCK_TO_TICKER = {
    # ── KOSPI 대형주 ──
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940",
    "현대차": "005380",
    "기아": "000270",
    "셀트리온": "068270",
    "NAVER": "035420",
    "카카오": "035720",
    "삼성SDI": "006400",
    "LG화학": "051910",
    "POSCO홀딩스": "005490",
    "현대모비스": "012330",
    "KB금융": "105560",
    "신한지주": "055550",
    "삼성물산": "028260",
    "삼성생명": "032830",
    "하나금융지주": "086790",
    "LG전자": "066570",
    "SK이노베이션": "096770",
    "한국전력": "015760",
    "KT&G": "033780",
    "삼성전기": "009150",
    # ── KOSDAQ 대형주 ──
    "에코프로비엠": "247540",
    "에코프로": "086520",
    "알테오젠": "196170",
    "HLB": "028300",
    # ── ETF (기술적 추천 대상 제외 — 항상 맨 끝 2개) ──
    "KODEX 200": "069500",
    "KODEX 코스닥150": "229200",
}

# 종목코드 → 시장 구분 (KOSPI / KOSDAQ). KIS 시세 API 는 둘 다 "J" 사용하지만
# 표시/필터링용으로 보관.
TICKER_TO_MARKET = {
    "005930": "KOSPI", "000660": "KOSPI", "373220": "KOSPI", "207940": "KOSPI",
    "005380": "KOSPI", "000270": "KOSPI", "068270": "KOSPI", "035420": "KOSPI",
    "035720": "KOSPI", "006400": "KOSPI", "051910": "KOSPI", "005490": "KOSPI",
    "012330": "KOSPI", "105560": "KOSPI", "055550": "KOSPI", "028260": "KOSPI",
    "032830": "KOSPI", "086790": "KOSPI", "066570": "KOSPI", "096770": "KOSPI",
    "015760": "KOSPI", "033780": "KOSPI", "009150": "KOSPI",
    "247540": "KOSDAQ", "086520": "KOSDAQ", "196170": "KOSDAQ", "028300": "KOSDAQ",
    "069500": "KOSPI", "229200": "KOSPI",  # ETF 는 KOSPI 상장
}

# KIS 국내주식 시세 API 시장분류코드 (FID_COND_MRKT_DIV_CODE)
#   J: 주식/ETF/ETN (KOSPI·KOSDAQ 공용)
KIS_MARKET_DIV_CODE = "J"

# Yahoo Finance 접미사 (.KS: 코스피, .KQ: 코스닥)
YAHOO_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}


def yahoo_symbol(ticker: str) -> str:
    """6자리 종목코드 → Yahoo Finance 심볼 (예: 005930 → 005930.KS)"""
    market = TICKER_TO_MARKET.get(ticker, "KOSPI")
    return f"{ticker}{YAHOO_SUFFIX.get(market, '.KS')}"


# ══════════════════════════════════════════════════════════════════
# DB 테이블명 (_kor 접미사로 미국주식 테이블과 완전 분리)
# ══════════════════════════════════════════════════════════════════

TABLE_ECONOMIC = "economic_and_stock_data_kor"
TABLE_STOCK_RECOMMENDATIONS = "stock_recommendations_kor"
TABLE_STOCK_ANALYSIS = "stock_analysis_results_kor"
TABLE_SENTIMENT = "ticker_sentiment_analysis_kor"
TABLE_TRADE_RECORDS = "trade_records_kor"
TABLE_LLM_LOGS = "llm_decision_logs_kor"
# access_tokens 는 계좌 단위(국내·해외 공용)이므로 _kor 분리 없이 공용 사용
TABLE_ACCESS_TOKENS = "access_tokens"

# ══════════════════════════════════════════════════════════════════
# 한국 시장 시간 (KST, Asia/Seoul)
#   정규장: 09:00 ~ 15:30
# ══════════════════════════════════════════════════════════════════

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 0
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
# 자동 매수 실행 시간 (장 시작 직후 09:05~09:10)
BUY_WINDOW_START = (9, 5)
BUY_WINDOW_END = (9, 10)


# ══════════════════════════════════════════════════════════════════
# 호가 단위 (tick size) — 2023.01 KRX 호가가격단위 개편 기준 (KOSPI·KOSDAQ 통일)
# ══════════════════════════════════════════════════════════════════

def get_tick_size(price: float) -> int:
    """주가 구간별 호가 단위 반환."""
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_to_tick(price: float) -> int:
    """주가를 가장 가까운 호가 단위로 정렬 (지정가 주문 거부 방지). 정수(원) 반환."""
    if price <= 0:
        return 0
    tick = get_tick_size(price)
    return int(round(price / tick) * tick)

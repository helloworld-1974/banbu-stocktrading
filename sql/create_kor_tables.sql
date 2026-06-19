-- ════════════════════════════════════════════════════════════════
-- 한국주식 자동매매(app_kor) 전용 테이블 (_kor 접미사)
--   미국주식 테이블과 완전히 분리. access_tokens 만 계좌 단위로 공용.
--   Supabase SQL Editor 에서 실행.
-- ════════════════════════════════════════════════════════════════

-- ── 1. 경제 + 주가 데이터 ──────────────────────────────────────
-- 컬럼명은 stock_kor.py 의 지표/종목 한글명과 일치해야 함.
CREATE TABLE IF NOT EXISTS economic_and_stock_data_kor (
    id              BIGSERIAL PRIMARY KEY,
    "날짜"          DATE UNIQUE NOT NULL,
    -- 거시 지표 (FRED)
    "미국 기준금리"              DOUBLE PRECISION,
    "미국 장단기 금리차"          DOUBLE PRECISION,
    "미국 10년 국채금리"          DOUBLE PRECISION,
    "미국 2년 국채금리"           DOUBLE PRECISION,
    "달러 인덱스"                DOUBLE PRECISION,
    "미국 10년 기대 인플레이션율"  DOUBLE PRECISION,
    "한국 장기국채금리"           DOUBLE PRECISION,
    "한국 소비자물가지수"         DOUBLE PRECISION,
    "한국 수출"                  DOUBLE PRECISION,
    -- 지수 / 환율 (Yahoo)
    "코스피"                    DOUBLE PRECISION,
    "코스닥"                    DOUBLE PRECISION,
    "달러/원"                   DOUBLE PRECISION,
    "VIX 지수"                  DOUBLE PRECISION,
    "S&P 500 지수"              DOUBLE PRECISION,
    "나스닥 종합지수"            DOUBLE PRECISION,
    "닛케이 225"                DOUBLE PRECISION,
    "상해종합"                  DOUBLE PRECISION,
    "항셍"                      DOUBLE PRECISION,
    "금 가격"                   DOUBLE PRECISION,
    "달러 인덱스(DXY)"           DOUBLE PRECISION,
    "미국 10년 국채 ETF"         DOUBLE PRECISION,
    -- 개별 종목 (KOSPI/KOSDAQ 종가)
    "삼성전자"          DOUBLE PRECISION,
    "SK하이닉스"        DOUBLE PRECISION,
    "LG에너지솔루션"     DOUBLE PRECISION,
    "삼성바이오로직스"   DOUBLE PRECISION,
    "현대차"            DOUBLE PRECISION,
    "기아"              DOUBLE PRECISION,
    "셀트리온"          DOUBLE PRECISION,
    "NAVER"            DOUBLE PRECISION,
    "카카오"            DOUBLE PRECISION,
    "삼성SDI"          DOUBLE PRECISION,
    "LG화학"           DOUBLE PRECISION,
    "POSCO홀딩스"      DOUBLE PRECISION,
    "현대모비스"        DOUBLE PRECISION,
    "KB금융"           DOUBLE PRECISION,
    "신한지주"          DOUBLE PRECISION,
    "삼성물산"          DOUBLE PRECISION,
    "삼성생명"          DOUBLE PRECISION,
    "하나금융지주"      DOUBLE PRECISION,
    "LG전자"           DOUBLE PRECISION,
    "SK이노베이션"      DOUBLE PRECISION,
    "한국전력"          DOUBLE PRECISION,
    "KT&G"             DOUBLE PRECISION,
    "삼성전기"          DOUBLE PRECISION,
    "에코프로비엠"      DOUBLE PRECISION,
    "에코프로"          DOUBLE PRECISION,
    "알테오젠"          DOUBLE PRECISION,
    "HLB"              DOUBLE PRECISION,
    "KODEX 200"        DOUBLE PRECISION,
    "KODEX 코스닥150"   DOUBLE PRECISION
);

-- ── 2. 기술적 지표 추천 ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_recommendations_kor (
    id              BIGSERIAL PRIMARY KEY,
    "날짜"          DATE NOT NULL,
    "종목"          TEXT NOT NULL,
    "SMA20"         DOUBLE PRECISION,
    "SMA50"         DOUBLE PRECISION,
    "골든_크로스"    BOOLEAN,
    "RSI"           DOUBLE PRECISION,
    "MACD"          DOUBLE PRECISION,
    "Signal"        DOUBLE PRECISION,
    "MACD_매수_신호" BOOLEAN,
    "추천_여부"      BOOLEAN,
    volume_ratio    DOUBLE PRECISION,
    adx             DOUBLE PRECISION,
    daily_change_pct DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stock_recommendations_kor_date ON stock_recommendations_kor ("날짜");

-- ── 3. ML 예측 결과 ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_analysis_results_kor (
    id                      BIGSERIAL PRIMARY KEY,
    "Stock"                 TEXT NOT NULL,
    "Accuracy (%)"          DOUBLE PRECISION,
    "Rise Probability (%)"  DOUBLE PRECISION,
    "Last Actual Price"     DOUBLE PRECISION,
    "Predicted Future Price" DOUBLE PRECISION,
    "Recommendation"        TEXT,
    "Analysis"              TEXT,
    created_at              TIMESTAMPTZ DEFAULT now()
);

-- ── 4. 뉴스 감성 분석 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticker_sentiment_analysis_kor (
    id                      BIGSERIAL PRIMARY KEY,
    ticker                  TEXT NOT NULL,
    average_sentiment_score DOUBLE PRECISION,
    article_count           INTEGER,
    calculation_date        TEXT,
    created_at              TIMESTAMPTZ DEFAULT now()
);

-- ── 5. 거래 기록 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_records_kor (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    stock_name          TEXT,
    buy_price           DOUBLE PRECISION,
    buy_date            TEXT,
    quantity            INTEGER,
    holding_quantity    INTEGER DEFAULT 0,
    atr                 DOUBLE PRECISION,
    take_profit_price   DOUBLE PRECISION,
    stop_loss_price     DOUBLE PRECISION,
    status              TEXT,           -- buy_ordered / holding / sell_ordered / sold / buy_failed
    composite_score     DOUBLE PRECISION,
    account_type        TEXT,           -- mock / real
    sell_price          DOUBLE PRECISION,
    sell_date           TEXT,
    sell_reason         TEXT,
    profit_loss         DOUBLE PRECISION,
    profit_loss_pct     DOUBLE PRECISION,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trade_records_kor_status ON trade_records_kor (status, account_type);

-- ── 6. LLM 판단 로그 ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS llm_decision_logs_kor (
    id                  BIGSERIAL PRIMARY KEY,
    decision_date       DATE NOT NULL,
    ticker              TEXT NOT NULL,
    stock_name          TEXT,
    decision            TEXT,           -- BUY / HOLD / FAIL / N/A
    reason              TEXT,
    market_analysis     TEXT,
    composite_score     DOUBLE PRECISION,
    rise_probability    DOUBLE PRECISION,
    rsi                 DOUBLE PRECISION,
    adx                 DOUBLE PRECISION,
    vix_value           DOUBLE PRECISION,
    updated_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (decision_date, ticker)
);

-- ── 7. 종목 마스터 (선택) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS stocks_kor (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT UNIQUE NOT NULL,   -- 6자리 종목코드
    name        TEXT,
    market      TEXT,                   -- KOSPI / KOSDAQ
    created_at  TIMESTAMPTZ DEFAULT now()
);

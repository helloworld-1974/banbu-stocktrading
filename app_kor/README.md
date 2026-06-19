# app_kor — 한국주식 자동매매

기존 `app/`(미국 해외주식) 시스템을 **국내주식(KOSPI/KOSDAQ)** 용으로 미러링한 모듈입니다.
한국투자증권(KIS) **국내주식 API** 로 잔고 조회·주문을 수행하고, 멀티소스 시세 데이터로
기술적 분석·ML·감성·LLM 검토를 거쳐 자동 매수/매도합니다.

## 실행

```bash
# 한국주식 서버 (포트 8001 — 미국주식 app 은 8000)
python run_kor.py
```

## app 과의 주요 차이

| 항목 | app (미국) | app_kor (한국) |
|---|---|---|
| 거래 API | 해외주식 (overseas-stock) | **국내주식 (domestic-stock)** |
| 주문 TR | TTTT1002U/TTTT1006U | **TTTC0802U(매수)/TTTC0801U(매도)** |
| 잔고 TR | TTTS3012R | **TTTC8434R** |
| 통화 | USD ($) | **KRW (원)**, 호가단위 정렬(`round_to_tick`) |
| 종목코드 | 티커 (AAPL) | **6자리 코드 (005930)** |
| 시세 데이터 | KIS 해외 dailyprice | **멀티소스: KIS → pykrx → Yahoo(.KS/.KQ)** |
| 시장 시간 | ET 09:30~16:00 | **KST 09:00~15:30** |
| 자동 매수 | ET 10:30~10:35 | **KST 09:05~09:10** |
| 일일 파이프라인 | KST 21:00 | **KST 18:00** |
| 경제 데이터 수집 | KST 06:05 | **KST 16:30** |
| DB 테이블 | (공용) | **`*_kor` 별도 테이블** |

## 데이터 소스 우선순위

`.env` 의 `KOR_DATA_SOURCE_PRIORITY` (기본 `kis,pykrx,yahoo`) 순서로 시도하고
실패/빈 응답 시 다음 소스로 폴백합니다. `app_kor/services/market_data_service.py` 참조.
- `kis`: KIS 국내주식 기간별시세 (토큰 재사용, 별도 라이브러리 불필요)
- `pykrx`: `pip install pykrx` 필요
- `yahoo`: 추가 의존성 없음 (`.KS`/`.KQ` 접미사)

## 사전 준비

1. **DB 테이블 생성**: `sql/create_kor_tables.sql` 을 Supabase SQL Editor 에서 실행
2. **.env 추가 설정 (선택)**:
   - `SLACK_WEBHOOK_URL_KOR` — 한국주식 전용 Slack 채널 (없으면 공용 `SLACK_WEBHOOK_URL`)
   - `KAGGLE_KERNEL_SLUG_KOR` / `KAGGLE_NOTEBOOK_DIR_KOR` — 한국주식 ML 커널 (기본 `stock-prediction-kor` / `kaggle_notebook_kor`)
   - `KOR_DATA_SOURCE_PRIORITY`, `KOR_INVEST_RATIO` (기본 0.10)
   - KIS 앱키/계좌는 `app` 과 공용 (`KIS_*`) — 국내·해외 동일 계좌
3. **ML 노트북**: `kaggle_notebook_kor/` 에 `kernel-metadata.json` + `predict.py`(한국 종목 학습용) 준비 후 한 번 수동 push

## 구조

```
app_kor/
├── main.py                  FastAPI 앱 + 스케줄러 기동
├── core/
│   ├── config.py            설정 (KIS 공용 + 한국 전용)
│   └── constants.py         종목 유니버스 / 테이블명 / 호가단위 / 장시간
├── db/supabase.py
├── services/
│   ├── balance_service.py        KIS 국내주식 잔고/주문/현재가
│   ├── market_data_service.py    멀티소스 OHLCV (kis/pykrx/yahoo)
│   ├── volume_service.py         거래량 조회
│   ├── scoring_service.py        composite_score (v1/v2)
│   ├── stock_recommendation_service.py  기술분석·추천·매도판단
│   ├── economic_service.py       한국 경제/주가 수집·저장
│   ├── ml_trigger_service.py     Kaggle ML 트리거
│   ├── notification_service.py   Slack 알림 (원화)
│   └── llm_review_service.py     Claude 매수 검토 (한국 시장 프롬프트)
├── api/routes/              balance/economic/stock_recommendations/stocks/volume/llm_review/pipeline
└── utils/scheduler.py       자동 매수/매도/파이프라인 (KST)
stock_kor.py                 (루트) 한국 경제/주가 수집 모듈
run_kor.py                   (루트) 진입점 (포트 8001)
sql/create_kor_tables.sql    _kor 테이블 마이그레이션
```

## 주의

- **감성 분석**: AlphaVantage NEWS_SENTIMENT 는 한국 6자리 종목코드를 직접 지원하지
  않을 수 있어 결과가 비어있을 수 있습니다 (점수 로직은 `sentiment_score=None` 을 안전 처리).
  국내 뉴스 감성 소스 연동은 향후 개선 과제입니다.
- **VIX**: 한국 전용 VKOSPI 대신 글로벌 공포지수(^VIX)를 `VIX 지수` 컬럼으로 사용합니다.
- 종목 유니버스는 `app_kor/core/constants.py` 의 `STOCK_TO_TICKER` 에서 조정하세요
  (조정 시 `sql/create_kor_tables.sql` 의 `economic_and_stock_data_kor` 컬럼도 함께 추가).
```

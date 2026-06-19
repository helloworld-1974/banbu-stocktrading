"""
한국주식 기술적 분석 + 추천 + 매도 판단 서비스.

미국판(app/services/stock_recommendation_service.py) 을 국내주식용으로 치환:
  - 종목 유니버스: app_kor.core.constants.STOCK_TO_TICKER (6자리 종목코드)
  - 잔고: balance_service.get_all_balances() — 국내주식 필드(pdno/hldg_qty/prpr/...)
  - 일봉: volume_service.get_daily_price() — 멀티소스(kis/pykrx/yahoo) 정규화
  - 시간대: KST (정규장 09:00~15:30)
  - 호가: round_to_tick (원 단위, 호가단위 정렬)
  - 테이블: *_kor
"""
import pandas as pd
import requests
import time
import pytz
from datetime import datetime, timedelta

from app_kor.db.supabase import supabase
from app_kor.core.config import settings
from app_kor.core.constants import (
    STOCK_TO_TICKER, round_to_tick,
    TABLE_ECONOMIC, TABLE_STOCK_RECOMMENDATIONS, TABLE_STOCK_ANALYSIS,
    TABLE_SENTIMENT, TABLE_TRADE_RECORDS,
)
from app_kor.services.balance_service import get_all_balances, current_account_type
from app_kor.services.volume_service import get_daily_price
from app_kor.services.scoring_service import score_and_filter

KST = pytz.timezone('Asia/Seoul')


class StockRecommendationService:
    def __init__(self):
        # ETF(맨 끝 2개) 제외한 종목명 리스트
        self.stock_columns = list(STOCK_TO_TICKER.keys())[:-2]
        self.lookback_days = 180  # 6개월 데이터

    # ── 지표 계산 ──────────────────────────────────────────────

    def calculate_sma(self, series, period):
        return series.rolling(window=period).mean()

    def calculate_ema(self, series, period):
        return series.ewm(span=period, adjust=False).mean()

    def calculate_rsi(self, series, period=14):
        """RSI 계산 (Wilder's Smoothing)."""
        trading_series = series[series.diff() != 0].copy()
        if len(series) > 0:
            trading_series = pd.concat([series.iloc[:1], trading_series]).drop_duplicates()

        if len(trading_series) < period + 1:
            return pd.Series([50] * len(series), index=series.index)

        delta = trading_series.diff().dropna()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.reindex(series.index, method='ffill')
        return rsi

    def calculate_macd(self, series, short_period=12, long_period=26, signal_period=9):
        short_ema = self.calculate_ema(series, short_period)
        long_ema = self.calculate_ema(series, long_period)
        macd = short_ema - long_ema
        signal = self.calculate_ema(macd, signal_period)
        return macd, signal

    def calculate_atr(self, daily_data, period=14):
        """일봉 데이터로 ATR 계산 (output2 latest-first, 키: high/low/clos)."""
        try:
            if len(daily_data) < period + 1:
                return None
            data = list(reversed(daily_data))
            highs = [float(d.get("high", "0") or "0") for d in data]
            lows = [float(d.get("low", "0") or "0") for d in data]
            closes = [float(d.get("clos", "0") or "0") for d in data]

            if any(v == 0 for v in closes[:period + 1]):
                return None

            tr_list = []
            for i in range(1, len(data)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                tr_list.append(tr)

            if len(tr_list) < period:
                return None

            atr = sum(tr_list[:period]) / period
            for i in range(period, len(tr_list)):
                atr = (atr * (period - 1) + tr_list[i]) / period
            return round(atr, 2)
        except Exception as e:
            print(f"  ATR 계산 오류: {e}")
            return None

    def calculate_adx(self, daily_data, period=14):
        """일봉 데이터로 ADX 계산."""
        try:
            if len(daily_data) < period * 2 + 1:
                return None
            data = list(reversed(daily_data))
            highs = [float(d.get("high", "0") or "0") for d in data]
            lows = [float(d.get("low", "0") or "0") for d in data]
            closes = [float(d.get("clos", "0") or "0") for d in data]

            if any(v == 0 for v in closes[:period * 2]):
                return None

            tr_list, plus_dm_list, minus_dm_list = [], [], []
            for i in range(1, len(data)):
                high_diff = highs[i] - highs[i - 1]
                low_diff = lows[i - 1] - lows[i]
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0
                minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0
                tr_list.append(tr)
                plus_dm_list.append(plus_dm)
                minus_dm_list.append(minus_dm)

            atr = sum(tr_list[:period])
            plus_di_smooth = sum(plus_dm_list[:period])
            minus_di_smooth = sum(minus_dm_list[:period])

            dx_list = []
            for i in range(period, len(tr_list)):
                atr = atr - (atr / period) + tr_list[i]
                plus_di_smooth = plus_di_smooth - (plus_di_smooth / period) + plus_dm_list[i]
                minus_di_smooth = minus_di_smooth - (minus_di_smooth / period) + minus_dm_list[i]
                if atr == 0:
                    continue
                plus_di = 100 * plus_di_smooth / atr
                minus_di = 100 * minus_di_smooth / atr
                di_sum = plus_di + minus_di
                dx_list.append(0 if di_sum == 0 else 100 * abs(plus_di - minus_di) / di_sum)

            if len(dx_list) < period:
                return None
            adx = sum(dx_list[:period]) / period
            for i in range(period, len(dx_list)):
                adx = (adx * (period - 1) + dx_list[i]) / period
            return round(adx, 2)
        except Exception as e:
            print(f"  ADX 계산 오류: {e}")
            return None

    # ── 기술적 추천 생성 ────────────────────────────────────────

    def generate_technical_recommendations(self):
        """기술적 지표 기반 추천 데이터 생성 후 *_kor 테이블에 저장."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.lookback_days)
        start_date_str = start_date.strftime("%Y-%m-%d")

        quoted_columns = [f'"{col}"' for col in self.stock_columns]
        quoted_columns.append('"날짜"')

        response = supabase.table(TABLE_ECONOMIC) \
            .select(*quoted_columns) \
            .gte("날짜", start_date_str) \
            .order("날짜") \
            .execute()

        if not response.data:
            return {"message": "데이터가 없습니다", "data": []}

        df = pd.DataFrame(response.data)
        df["날짜"] = pd.to_datetime(df["날짜"])
        df.set_index("날짜", inplace=True)
        df = df.astype(float)
        df.ffill(inplace=True)
        df.bfill(inplace=True)

        now_kst = datetime.now(KST)
        today_kst_str = now_kst.strftime("%Y%m%d")
        # 정규장 마감(15:30 KST) 이후면 당일 데이터 완료로 간주
        is_market_closed = (now_kst.hour > 15) or (now_kst.hour == 15 and now_kst.minute >= 30)

        recommendations = []
        for stock in self.stock_columns:
            prices = df[stock]

            sma20 = self.calculate_sma(prices, 20)
            sma50 = self.calculate_sma(prices, 50)
            golden_cross = sma20 > sma50
            rsi = self.calculate_rsi(prices)
            macd, signal = self.calculate_macd(prices)
            macd_buy_signal = macd > signal
            rsi_val = rsi.iloc[-1] if not rsi.empty else 50
            rsi_buy = rsi_val <= 65
            recommended = golden_cross & rsi_buy & macd_buy_signal

            ticker = STOCK_TO_TICKER.get(stock)
            volume_ratio = None
            adx = None
            daily_change_pct = None
            if ticker:
                try:
                    vol_result = get_daily_price(ticker)
                    if vol_result and vol_result.get("rt_cd") == "0":
                        raw_daily_data = vol_result.get("output2", [])
                        # 실제 거래일만 필터 (주말 제외 + tvol>0)
                        daily_data = []
                        for d in raw_daily_data:
                            xymd = d.get("xymd", "")
                            tvol = int(d.get("tvol", "0") or "0")
                            if xymd and len(xymd) == 8 and tvol > 0:
                                try:
                                    dt = datetime.strptime(xymd, "%Y%m%d")
                                    if dt.weekday() < 5:
                                        daily_data.append(d)
                                except ValueError:
                                    pass
                        print(f"  {ticker} 거래일 필터: {len(raw_daily_data)}일 → {len(daily_data)}일")
                        # 1차 가드: 당일 미완료 데이터 제외
                        if len(daily_data) >= 2 and not is_market_closed:
                            if daily_data[0].get("xymd", "") == today_kst_str:
                                print(f"  {ticker} 오늘({today_kst_str}) 데이터는 장 마감 전 미완료 → 제외")
                                daily_data = daily_data[1:]
                        # 2차 가드: 거래량 10% 미만이면 미완료
                        if len(daily_data) >= 7:
                            first_vol = int(daily_data[0].get("tvol", "0") or "0")
                            second_vol = int(daily_data[1].get("tvol", "0") or "0")
                            if second_vol > 0 and first_vol < second_vol * 0.10:
                                daily_data = daily_data[1:]
                        if len(daily_data) >= 6:
                            today_vol = int(daily_data[0].get("tvol", "0") or "0")
                            past_vols = [int(d.get("tvol", "0") or "0") for d in daily_data[1:6]]
                            past_vols = [v for v in past_vols if v > 0]
                            if past_vols:
                                avg_vol = sum(past_vols) / len(past_vols)
                                volume_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else None
                                print(f"  {ticker} volume_ratio={volume_ratio}")
                        adx = self.calculate_adx(daily_data if daily_data else raw_daily_data)
                        if len(daily_data) >= 2:
                            today_close = float(daily_data[0].get("clos", "0") or "0")
                            prev_close = float(daily_data[1].get("clos", "0") or "0")
                            if prev_close > 0 and today_close > 0:
                                daily_change_pct = round(((today_close - prev_close) / prev_close) * 100, 2)
                    time.sleep(0.3)  # 멀티소스 rate-limit 완화
                except Exception as e:
                    print(f"  {ticker} 거래량/ADX 조회 실패: {e}")

            latest_date = df.index[-1]
            if all(pd.notna([sma20[latest_date], sma50[latest_date], rsi[latest_date], macd[latest_date], signal[latest_date]])):
                recommendations.append({
                    "날짜": latest_date.strftime("%Y-%m-%d"),
                    "종목": stock,
                    "SMA20": float(sma20[latest_date]),
                    "SMA50": float(sma50[latest_date]),
                    "골든_크로스": bool(golden_cross[latest_date]),
                    "RSI": float(rsi[latest_date]),
                    "MACD": float(macd[latest_date]),
                    "Signal": float(signal[latest_date]),
                    "MACD_매수_신호": bool(macd_buy_signal[latest_date]),
                    "추천_여부": bool(recommended[latest_date]),
                    "volume_ratio": volume_ratio,
                    "adx": adx,
                    "daily_change_pct": daily_change_pct,
                })

        try:
            supabase.table(TABLE_STOCK_RECOMMENDATIONS).delete().gte("날짜", "1900-01-01").execute()
            supabase.table(TABLE_STOCK_RECOMMENDATIONS).insert(recommendations).execute()
        except Exception as e:
            print(f"오류 발생: {str(e)}")
            import traceback
            print(traceback.format_exc())
            raise Exception(f"추천 주식 분석 중 오류: {str(e)}")

        return {"message": f"{len(recommendations)}개의 추천 데이터가 생성되었습니다", "data": recommendations}

    # ── ML 예측 결과 조회 ──────────────────────────────────────

    def get_stock_recommendations(self):
        """ML 예측(stock_analysis_results_kor)에서 상승확률 >= 2% 종목 반환."""
        response = supabase.table(TABLE_STOCK_ANALYSIS).select("*").order("created_at", desc=True).execute()
        if not response.data:
            return {"message": "분석 결과를 찾을 수 없습니다", "recommendations": []}

        df = pd.DataFrame(response.data)
        for col in ['Accuracy (%)', 'Rise Probability (%)']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        filtered_df = df[(df['Rise Probability (%)'] >= 2)]
        filtered_df = filtered_df.sort_values(by='Rise Probability (%)', ascending=False)
        result_columns = [
            'Stock', 'Accuracy (%)', 'Rise Probability (%)', 'Last Actual Price',
            'Predicted Future Price', 'Recommendation', 'Analysis'
        ]
        result_df = filtered_df[result_columns]
        recommendations = result_df.to_dict(orient='records')
        return {
            "message": f"{len(recommendations)}개의 추천 주식을 찾았습니다",
            "recommendations": recommendations,
        }

    def get_recommendations_with_sentiment(self):
        """ML 추천 + 감성(>= 0.15) 결합."""
        stock_recs = self.get_stock_recommendations()
        recommendations = stock_recs.get("recommendations", [])
        if not recommendations:
            return {"message": "추천 주식이 없습니다", "results": []}

        sentiment_response = supabase.table(TABLE_SENTIMENT).select("*").gte("average_sentiment_score", 0.15).execute()
        if not sentiment_response.data:
            return {"message": "감정 분석 데이터가 없습니다", "results": []}

        ticker_to_recommendation = {
            STOCK_TO_TICKER.get(rec["Stock"]): rec
            for rec in recommendations if rec["Stock"] in STOCK_TO_TICKER
        }
        sentiment_data = {item["ticker"]: item for item in sentiment_response.data}

        results = []
        for ticker, sentiment in sentiment_data.items():
            if ticker in ticker_to_recommendation:
                recommendation = ticker_to_recommendation[ticker]
                results.append({
                    "ticker": ticker,
                    "stock_name": recommendation["Stock"],
                    "accuracy": recommendation["Accuracy (%)"],
                    "rise_probability": recommendation["Rise Probability (%)"],
                    "last_actual_price": recommendation["Last Actual Price"],
                    "predicted_future_price": recommendation["Predicted Future Price"],
                    "recommendation": recommendation["Recommendation"],
                    "analysis": recommendation["Analysis"],
                    "average_sentiment_score": sentiment["average_sentiment_score"],
                    "article_count": sentiment["article_count"],
                    "calculation_date": sentiment["calculation_date"],
                })

        return {"message": f"{len(results)}개의 추천 주식을 분석했습니다", "results": results}

    # ── 뉴스 감성 분석 (AlphaVantage) ──────────────────────────

    def fetch_and_store_sentiment_for_recommendations(self):
        """추천+보유 종목에 대해 뉴스 감성 데이터 수집 후 *_kor 에 저장.

        주의: AlphaVantage NEWS_SENTIMENT 는 한국 6자리 종목코드를 직접 지원하지
        않을 수 있다 (대부분 미국 상장 ticker 기준). 미국판 구조를 유지하되,
        결과가 없으면 sentiment_score=None 으로 downstream 점수 로직이 처리한다.
        """
        stock_recs = self.get_stock_recommendations()
        recommendations = stock_recs.get("recommendations", [])
        recommended_tickers = [STOCK_TO_TICKER.get(rec["Stock"]) for rec in recommendations if rec["Stock"] in STOCK_TO_TICKER]

        balance_result = get_all_balances()
        holdings = balance_result.get("output1", []) if balance_result.get("rt_cd") == "0" else []
        holding_tickers = [item.get("pdno") for item in holdings if item.get("pdno")]

        all_tickers = list(set(recommended_tickers + holding_tickers))
        if not all_tickers:
            return {"message": "분석할 티커가 없습니다", "results": []}

        print(f"분석할 티커 목록 ({len(all_tickers)}개): {all_tickers}")

        api_key = settings.ALPHA_VANTAGE_API_KEY
        relevance_threshold = 0.2
        sleep_interval = 5
        time_from = (datetime.now() - timedelta(days=3)).strftime("%Y%m%dT0000")
        base_url = "https://www.alphavantage.co/query"
        params = {"function": "NEWS_SENTIMENT", "time_from": time_from, "limit": 100, "apikey": api_key}

        ticker_to_stock = {ticker: stock for stock, ticker in STOCK_TO_TICKER.items()}
        recommendations_by_ticker = {STOCK_TO_TICKER[rec["Stock"]]: rec for rec in recommendations if rec["Stock"] in STOCK_TO_TICKER}
        holdings_by_ticker = {item.get("pdno"): item for item in holdings if item.get("pdno")}

        print("기존 감정 분석 데이터 삭제 중...")
        supabase.table(TABLE_SENTIMENT).delete().gte("ticker", "").execute()

        results = []
        for ticker in all_tickers:
            print(f"{ticker} 처리 중...")
            params["tickers"] = ticker
            try:
                response = requests.get(base_url, params=params)
            except Exception as e:
                results.append({"ticker": ticker, "stock_name": ticker_to_stock.get(ticker, ticker), "message": f"API 호출 예외: {e}"})
                time.sleep(sleep_interval)
                continue

            if response.status_code != 200:
                results.append({
                    "ticker": ticker, "stock_name": ticker_to_stock.get(ticker, ticker),
                    "message": "API 호출 실패",
                    "is_recommended": ticker in recommended_tickers,
                    "is_holding": ticker in holding_tickers,
                })
                time.sleep(sleep_interval)
                continue

            feed = response.json().get('feed', [])
            articles = [
                float(s['ticker_sentiment_score'])
                for article in feed
                for s in article.get('ticker_sentiment', [])
                if s['ticker'] == ticker and float(s['relevance_score']) >= relevance_threshold
            ]

            if not articles:
                results.append({
                    "ticker": ticker, "stock_name": ticker_to_stock.get(ticker, ticker),
                    "message": "관련 기사 없음",
                    "is_recommended": ticker in recommended_tickers,
                    "is_holding": ticker in holding_tickers,
                })
                time.sleep(sleep_interval)
                continue

            average_sentiment = sum(articles) / len(articles)
            supabase.table(TABLE_SENTIMENT).insert({
                "ticker": ticker,
                "average_sentiment_score": average_sentiment,
                "article_count": len(articles),
                "calculation_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }).execute()

            results.append({
                "ticker": ticker, "stock_name": ticker_to_stock.get(ticker, ticker),
                "average_sentiment_score": average_sentiment, "article_count": len(articles),
                "is_recommended": ticker in recommended_tickers,
                "is_holding": ticker in holding_tickers,
            })
            time.sleep(sleep_interval)

        return {
            "message": f"{len(results)}개의 티커(추천: {len(recommended_tickers)}, 보유: {len(holding_tickers)})를 분석했습니다",
            "results": results,
        }

    # ── 통합 매수 후보 추출 ────────────────────────────────────

    def get_combined_recommendations_with_technical_and_sentiment(self):
        """ML + 기술 + 감성 + 시장환경 통합 매수 추천."""
        try:
            tech_response = supabase.table(TABLE_STOCK_RECOMMENDATIONS).select("*").order("날짜", desc=True).execute()
            if not tech_response.data:
                return {"message": "기술적 지표 데이터가 없습니다", "results": []}

            tech_df = pd.DataFrame(tech_response.data)
            tech_df["골든_크로스"] = tech_df["골든_크로스"].astype(bool)
            tech_df["MACD_매수_신호"] = tech_df["MACD_매수_신호"].astype(bool)
            tech_df["RSI"] = pd.to_numeric(tech_df["RSI"])

            stock_recs = self.get_stock_recommendations()
            recommendations = stock_recs.get("recommendations", [])
            if not recommendations:
                return {"message": "추천 주식이 없습니다", "results": []}

            sentiment_response = supabase.table(TABLE_SENTIMENT).select("*").execute()

            def _safe_value(v):
                try:
                    return None if pd.isna(v) else v
                except (ValueError, TypeError):
                    return v
            tech_map = {row["종목"]: {k: _safe_value(v) for k, v in row.to_dict().items()} for _, row in tech_df.iterrows()}
            sentiment_map = {item["ticker"]: item for item in sentiment_response.data} if sentiment_response.data else {}

            results = []
            for rec in recommendations:
                stock_name = rec["Stock"]
                if stock_name not in STOCK_TO_TICKER:
                    continue
                ticker = STOCK_TO_TICKER[stock_name]
                tech_data = tech_map.get(stock_name)
                if tech_data is None:
                    continue
                sentiment = sentiment_map.get(ticker)

                volume_ratio = tech_data.get("volume_ratio")
                if volume_ratio is not None:
                    volume_ratio = float(volume_ratio)
                adx_value = tech_data.get("adx")
                if adx_value is not None:
                    adx_value = float(adx_value)

                results.append({
                    "ticker": ticker,
                    "stock_name": stock_name,
                    "accuracy": rec["Accuracy (%)"],
                    "rise_probability": rec["Rise Probability (%)"],
                    "last_price": rec["Last Actual Price"],
                    "predicted_price": rec["Predicted Future Price"],
                    "recommendation": rec["Recommendation"],
                    "analysis": rec["Analysis"],
                    "sentiment_score": sentiment["average_sentiment_score"] if sentiment else None,
                    "article_count": sentiment["article_count"] if sentiment else None,
                    "sentiment_date": sentiment["calculation_date"] if sentiment else None,
                    "technical_date": tech_data["날짜"],
                    "sma20": float(tech_data["SMA20"]),
                    "sma50": float(tech_data["SMA50"]),
                    "golden_cross": bool(tech_data["골든_크로스"]),
                    "rsi": float(tech_data["RSI"]),
                    "macd": float(tech_data["MACD"]),
                    "signal": float(tech_data["Signal"]),
                    "macd_buy_signal": bool(tech_data["MACD_매수_신호"]),
                    "technical_recommended": bool(tech_data["추천_여부"]),
                    "volume_ratio": volume_ratio,
                    "adx": adx_value,
                })

            # VIX (글로벌 공포지수) 조회
            vix_value = None
            try:
                vix_response = supabase.table(TABLE_ECONOMIC).select("*").order("날짜", desc=True).limit(1).execute()
                if vix_response.data and vix_response.data[0].get("VIX 지수") is not None:
                    vix_value = float(vix_response.data[0]["VIX 지수"])
                    print(f"  VIX 지수: {vix_value}")
            except Exception as e:
                print(f"  VIX 조회 실패: {e}")

            if vix_value is not None and vix_value > 35:
                print(f"  VIX {vix_value:.1f} > 35: 공포장 매수 중단")
                return {"message": f"VIX {vix_value:.1f} - 공포장으로 매수를 중단합니다", "results": []}

            final_results = score_and_filter(
                candidates=results, vix_value=vix_value, use_v2=settings.USE_SCORING_V2,
            )
            version = "v2 (z-score)" if settings.USE_SCORING_V2 else "v1 (raw)"
            print(f"  점수 모드: {version}, 통과 종목: {len(final_results)}개")
            for c in final_results:
                print(f"  {c['stock_name']}({c['ticker']}) score={c['composite_score']:+.4f}")

            return {"message": f"{len(final_results)}개의 매수 추천 주식을 찾았습니다 ({version})", "results": final_results}
        except Exception as e:
            print(f"오류 발생: {str(e)}")
            import traceback
            print(traceback.format_exc())
            raise Exception(f"추천 주식 분석 중 오류: {str(e)}")

    # ── 매도 대상 판단 ─────────────────────────────────────────

    def get_stocks_to_sell(self, balance_result=None):
        """매도 대상 종목 식별 (ATR 익절/손절 + 기술 신호 + 감성 + VIX)."""
        try:
            if balance_result is None:
                balance_result = get_all_balances()
            if balance_result.get("rt_cd") != "0" or "output1" not in balance_result:
                return {"message": f"보유 종목 정보를 가져오는데 실패했습니다: {balance_result.get('msg1', '')}", "sell_candidates": []}

            holdings = balance_result.get("output1", [])
            if not holdings:
                return {"message": "보유 종목이 없습니다", "sell_candidates": []}

            print(f"보유 종목 {len(holdings)}개")

            # 기술적 지표 (최신, 종목별 1건)
            tech_response = supabase.table(TABLE_STOCK_RECOMMENDATIONS).select("*").order("날짜", desc=True).execute()
            tech_data = pd.DataFrame(tech_response.data) if tech_response.data else pd.DataFrame()
            if not tech_data.empty:
                tech_data["골든_크로스"] = tech_data["골든_크로스"].astype(bool)
                tech_data["MACD_매수_신호"] = tech_data["MACD_매수_신호"].astype(bool)
                tech_data["RSI"] = pd.to_numeric(tech_data["RSI"])
                tech_data = tech_data.sort_values("날짜", ascending=False).drop_duplicates(subset=["종목"], keep="first")

            sentiment_response = supabase.table(TABLE_SENTIMENT).select("*").execute()
            sentiment_data = {item["ticker"]: item for item in sentiment_response.data} if sentiment_response.data else {}

            vix_value = None
            try:
                vix_response = supabase.table(TABLE_ECONOMIC).select("*").order("날짜", desc=True).limit(1).execute()
                if vix_response.data and vix_response.data[0].get("VIX 지수") is not None:
                    vix_value = float(vix_response.data[0]["VIX 지수"])
            except Exception as e:
                print(f"  VIX 조회 실패: {e}")

            # trade_records 의 ATR 기준
            trade_records_map = {}
            try:
                tr_response = supabase.table(TABLE_TRADE_RECORDS).select("*").eq("status", "holding").eq("account_type", current_account_type()).execute()
                if tr_response.data:
                    for tr in tr_response.data:
                        trade_records_map[tr["ticker"]] = tr
            except Exception as e:
                print(f"trade_records 조회 실패 (고정 비율 폴백): {e}")

            # ATR/익절가/손절가 백필
            for ticker, tr in trade_records_map.items():
                if tr.get("take_profit_price") and tr.get("stop_loss_price"):
                    continue
                try:
                    buy_date_str = tr.get("buy_date") or ""
                    buy_ymd = buy_date_str[:10].replace("-", "") if len(buy_date_str) >= 10 else ""
                    vol_result = get_daily_price(ticker, end_date=buy_ymd)
                    if not (vol_result and vol_result.get("rt_cd") == "0"):
                        continue
                    atr_value = self.calculate_atr(vol_result.get("output2", []))
                    buy_price = float(tr.get("buy_price") or 0)
                    if not atr_value or buy_price <= 0:
                        continue
                    tp_price = round_to_tick(buy_price + atr_value * 2.5)
                    sl_price = round_to_tick(buy_price - atr_value * 1.5)
                    supabase.table(TABLE_TRADE_RECORDS).update({
                        "atr": atr_value, "take_profit_price": tp_price, "stop_loss_price": sl_price,
                    }).eq("id", tr["id"]).execute()
                    tr["atr"], tr["take_profit_price"], tr["stop_loss_price"] = atr_value, tp_price, sl_price
                    print(f"  {ticker} ATR 백필: ATR={atr_value}, 익절={tp_price}, 손절={sl_price}")
                except Exception as e:
                    print(f"  {ticker} ATR 백필 오류: {e}")

            sell_candidates = []
            ticker_to_stock = {v: k for k, v in STOCK_TO_TICKER.items()}

            for item in holdings:
                ticker = item.get("pdno")
                stock_name = item.get("prdt_name") or ticker_to_stock.get(ticker, ticker)
                purchase_price = float(item.get("pchs_avg_pric", 0) or 0)
                current_price = float(item.get("prpr", 0) or 0)
                quantity = int(item.get("hldg_qty", 0) or 0)

                price_change_percent = ((current_price - purchase_price) / purchase_price) * 100 if purchase_price > 0 else 0

                sell_reasons = []
                technical_sell_signals = 0

                # 조건 1: ATR 익절/손절
                trade_record = trade_records_map.get(ticker)
                if trade_record and trade_record.get("take_profit_price") and trade_record.get("stop_loss_price"):
                    tp_price = float(trade_record["take_profit_price"])
                    sl_price = float(trade_record["stop_loss_price"])
                    if current_price >= tp_price:
                        sell_reasons.append(f"ATR 익절 조건 충족: 현재가 {current_price:,.0f}원 >= 익절가 {tp_price:,.0f}원 ({price_change_percent:.2f}%)")
                    elif current_price <= sl_price:
                        sell_reasons.append(f"ATR 손절 조건 충족: 현재가 {current_price:,.0f}원 <= 손절가 {sl_price:,.0f}원 ({price_change_percent:.2f}%)")
                else:
                    if price_change_percent >= 5:
                        sell_reasons.append(f"익절 조건 충족: 구매가 대비 {price_change_percent:.2f}% 상승 (고정비율)")
                    elif price_change_percent <= -7:
                        sell_reasons.append(f"손절 조건 충족: 구매가 대비 {price_change_percent:.2f}% 하락 (고정비율)")

                tech_record = None
                if not tech_data.empty:
                    korean_name = ticker_to_stock.get(ticker)
                    if korean_name:
                        tech_filtered = tech_data[tech_data["종목"] == korean_name]
                        if not tech_filtered.empty:
                            tech_record = tech_filtered.iloc[0].to_dict()

                tech_sell_signals_details = []
                if tech_record:
                    if not tech_record["골든_크로스"]:
                        technical_sell_signals += 1
                        tech_sell_signals_details.append("데드 크로스")
                    if tech_record["RSI"] > 70:
                        technical_sell_signals += 1
                        tech_sell_signals_details.append(f"RSI 과매수({tech_record['RSI']:.2f})")
                    if not tech_record["MACD_매수_신호"]:
                        technical_sell_signals += 1
                        tech_sell_signals_details.append("MACD 매도 신호")
                    volume_ratio = tech_record.get("volume_ratio")
                    daily_change = tech_record.get("daily_change_pct")
                    if volume_ratio is not None and daily_change is not None and float(volume_ratio) >= 2.0 and float(daily_change) <= -3:
                        technical_sell_signals += 1
                        tech_sell_signals_details.append(f"패닉셀(거래량 {float(volume_ratio):.1f}배, 당일 {float(daily_change):.1f}% 하락)")

                adx_value = None
                if tech_record and tech_record.get("adx") is not None:
                    adx_value = float(tech_record["adx"])
                adx_adjustment = 1 if adx_value is not None and adx_value > 25 else 0

                sentiment_score = sentiment_data[ticker].get("average_sentiment_score") if ticker in sentiment_data else None

                required_signals_2b = 3 - adx_adjustment
                if technical_sell_signals >= required_signals_2b:
                    adx_note = f", ADX={adx_value:.1f} 보정" if adx_adjustment else ""
                    sell_reasons.append(f"기술적 매도 신호 {technical_sell_signals}개/{required_signals_2b}개: {', '.join(tech_sell_signals_details)}{adx_note}")
                elif sentiment_score is not None and sentiment_score < -0.15:
                    required_signals_2a = 2 - adx_adjustment
                    if technical_sell_signals >= required_signals_2a:
                        adx_note = f", ADX={adx_value:.1f} 보정" if adx_adjustment else ""
                        sell_reasons.append(f"부정적 감성({sentiment_score:.2f}) + 매도 신호 {technical_sell_signals}개/{required_signals_2a}개: {', '.join(tech_sell_signals_details)}{adx_note}")

                if vix_value is not None and technical_sell_signals >= 1:
                    if vix_value > 40 and technical_sell_signals >= 1:
                        sell_reasons.append(f"극단적 공포(VIX={vix_value:.1f}) + 매도 신호 {technical_sell_signals}개")
                    elif vix_value > 30 and technical_sell_signals >= 2:
                        sell_reasons.append(f"공포 시장(VIX={vix_value:.1f}) + 매도 신호 {technical_sell_signals}개")

                if sell_reasons:
                    sell_candidates.append({
                        "ticker": ticker,
                        "stock_name": stock_name,
                        "purchase_price": purchase_price,
                        "current_price": current_price,
                        "price_change_percent": price_change_percent,
                        "quantity": quantity,
                        "sell_reasons": sell_reasons,
                        "technical_sell_signals": technical_sell_signals,
                        "technical_sell_details": tech_sell_signals_details if tech_sell_signals_details else None,
                        "sentiment_score": sentiment_score,
                        "adx": adx_value,
                        "vix": vix_value,
                        "technical_data": tech_record,
                    })

            sell_candidates.sort(key=lambda x: abs(x["price_change_percent"]), reverse=True)
            return {"message": f"{len(sell_candidates)}개의 매도 대상 종목을 식별했습니다", "sell_candidates": sell_candidates}
        except Exception as e:
            print(f"매도 대상 종목 식별 중 오류 발생: {str(e)}")
            import traceback
            print(traceback.format_exc())
            return {"message": f"매도 대상 종목 식별 중 오류 발생: {str(e)}", "sell_candidates": []}

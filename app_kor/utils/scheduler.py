"""
한국주식 자동매매 스케줄러 (app_kor).

미국판(app/utils/scheduler.py) 을 한국 시장(KST)용으로 치환:
  - 자동 매수: 평일 09:05~09:10 KST (장 시작 직후), 5분 주기 체크, 1일 1회
  - 자동 매도: 평일 09:00~15:30 KST, 1분 주기 체크 + 주문 정합성(reconcile)
  - 경제 데이터 수집: 매일 16:30 KST (장 마감 후)
  - 일일 통합 파이프라인: 매일 18:00 KST (경제데이터 → Kaggle ML → 기술+감성 → LLM+매수)
  - 통화: 원(₩), 호가: round_to_tick, 잔고 필드: 국내주식(pdno/hldg_qty/prpr/...)
"""
import asyncio
import schedule
import time
import pytz
from datetime import datetime
import threading
import logging

from app_kor.services.stock_recommendation_service import StockRecommendationService
from app_kor.services.balance_service import (
    get_current_price, order_domestic_stock, get_all_balances,
    inquire_psbl_amount, current_account_type,
)
from app_kor.services.volume_service import get_daily_price
from app_kor.db.supabase import supabase
from app_kor.core.config import settings
from app_kor.core.constants import (
    round_to_tick, TABLE_TRADE_RECORDS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    BUY_WINDOW_START, BUY_WINDOW_END,
)
from app_kor.services.economic_service import update_economic_data_in_background
from app_kor.services.llm_review_service import review_buy_candidates
from app_kor.services.ml_trigger_service import trigger_and_wait
from app_kor.services.notification_service import (
    notify_data_ready, notify_llm_decisions, notify_buy_ordered, notify_buy_filled,
    notify_sell_ordered, notify_sell_filled, notify_pipeline_failure,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('stock_scheduler_kor.log')],
)
logger = logging.getLogger('stock_scheduler_kor')

KST = pytz.timezone('Asia/Seoul')


def _current_price(ticker: str):
    """KIS 국내주식 현재가(원) 조회. 실패 시 0.0."""
    try:
        result = get_current_price(ticker)
        if result.get("rt_cd") == "0":
            return float(result.get("output", {}).get("stck_prpr", 0) or 0)
        logger.error(f"{ticker} 현재가 조회 실패: {result.get('msg1', '')}")
    except Exception as e:
        logger.error(f"{ticker} 현재가 조회 예외: {e}")
    return 0.0


def _is_market_open(now_kst: datetime) -> bool:
    """평일 정규장(09:00~15:30 KST) 여부."""
    if now_kst.weekday() > 4:
        return False
    open_min = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE
    close_min = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    cur_min = now_kst.hour * 60 + now_kst.minute
    return open_min <= cur_min <= close_min


class StockScheduler:
    """한국주식 자동매매 스케줄러"""

    def __init__(self):
        self.recommendation_service = StockRecommendationService()
        self.running = False
        self.sell_running = False
        self.scheduler_thread = None
        self._last_buy_date = None

    # ── 스케줄러 라이프사이클 ───────────────────────────────────

    def start(self):
        if self.running:
            logger.warning("매수 스케줄러가 이미 실행 중입니다.")
            return False
        for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_auto_buy']:
            schedule.cancel_job(job)
        schedule.every(5).minutes.do(self._run_auto_buy)
        self.running = True
        self.scheduler_thread = threading.Thread(target=self._run_scheduler)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        logger.info("한국주식 자동매수 스케줄러 시작. 평일 09:05~09:10 KST 에 매수 실행.")
        return True

    def stop(self):
        if not self.running:
            logger.warning("매수 스케줄러가 실행 중이 아닙니다.")
            return False
        self.running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_auto_buy']:
            schedule.cancel_job(job)
        logger.info("매수 스케줄러가 중지되었습니다.")
        return True

    def start_sell_scheduler(self):
        if self.sell_running:
            logger.warning("매도 스케줄러가 이미 실행 중입니다.")
            return False
        for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_auto_sell']:
            schedule.cancel_job(job)
        schedule.every(1).minutes.do(self._run_auto_sell)
        if not self.running and not self.scheduler_thread:
            self.scheduler_thread = threading.Thread(target=self._run_scheduler)
            self.scheduler_thread.daemon = True
            self.scheduler_thread.start()
        self.sell_running = True
        logger.info("매도 스케줄러 시작. 1분마다 매도 대상 확인.")
        return True

    def stop_sell_scheduler(self):
        if not self.sell_running:
            logger.warning("매도 스케줄러가 실행 중이 아닙니다.")
            return False
        for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_auto_sell']:
            schedule.cancel_job(job)
        self.sell_running = False
        if not self.running and self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
            self.scheduler_thread = None
        logger.info("매도 스케줄러가 중지되었습니다.")
        return True

    def _run_scheduler(self):
        while self.running or self.sell_running:
            schedule.run_pending()
            time.sleep(1)

    def _run_auto_buy(self):
        try:
            asyncio.run(self._execute_auto_buy())
            return True
        except Exception as e:
            logger.error(f"자동 매수 작업 중 오류 발생: {str(e)}", exc_info=True)
            return False

    def _run_auto_sell(self):
        try:
            asyncio.run(self._execute_auto_sell())
            return True
        except Exception as e:
            logger.error(f"자동 매도 작업 중 오류 발생: {str(e)}", exc_info=True)
            return False

    # ── 주문 정합성 ─────────────────────────────────────────────

    def _reconcile_orders(self, balance_result=None):
        """KIS 원장 기준 주문 정합성 확인 (1분마다 평일 실행).

        국내주식 지정가 주문은 당일 장 마감 시 자동 취소(Day Order)됨.
        장 마감(15:30 KST) 후 버퍼(15:40~) 부터 미체결 정리.
        """
        try:
            active_response = supabase.table(TABLE_TRADE_RECORDS).select("*").in_(
                "status", ["buy_ordered", "sell_ordered", "holding"]
            ).eq("account_type", current_account_type()).execute()
            active_records = active_response.data if active_response.data else []

            if balance_result is None:
                balance_result = get_all_balances()
            if balance_result.get("rt_cd") != "0":
                logger.error(f"정합성 확인용 잔고 조회 실패: {balance_result.get('msg1', '')}")
                return

            kis_holdings = {}
            for item in balance_result.get("output1", []):
                ticker = item.get("pdno")
                qty = int(item.get("hldg_qty", 0) or 0)
                if ticker and qty > 0:
                    kis_holdings[ticker] = {"qty": qty, "item": item}

            now_kst = datetime.now(KST)
            # 장 마감(15:30) + 10분 버퍼 후 미체결 정리
            is_after_market_close = (now_kst.hour > 15) or (now_kst.hour == 15 and now_kst.minute >= 40)

            tracked_tickers = set()
            for record in active_records:
                ticker = record["ticker"]
                status = record["status"]
                record_id = record["id"]
                kis_qty = kis_holdings.get(ticker, {}).get("qty", 0)
                tracked_tickers.add(ticker)

                if status == "buy_ordered":
                    if kis_qty > 0:
                        supabase.table(TABLE_TRADE_RECORDS).update({
                            "status": "holding", "holding_quantity": kis_qty,
                        }).eq("id", record_id).execute()
                        logger.info(f"  {ticker} 매수 체결 확인 → holding ({kis_qty}주)")
                        try:
                            kis_item = kis_holdings.get(ticker, {}).get("item", {})
                            fill_price = float(kis_item.get("pchs_avg_pric", 0) or record.get("buy_price") or 0)
                            notify_buy_filled(
                                ticker=ticker, stock_name=record.get("stock_name", ticker),
                                qty=kis_qty, fill_price=fill_price,
                                take_profit_price=record.get("take_profit_price"),
                                stop_loss_price=record.get("stop_loss_price"),
                                composite_score=record.get("composite_score"),
                            )
                        except Exception as notify_e:
                            logger.warning(f"  {ticker} 매수 체결 알림 발송 실패: {notify_e}")
                    elif is_after_market_close:
                        supabase.table(TABLE_TRADE_RECORDS).update({"status": "buy_failed"}).eq("id", record_id).execute()
                        logger.warning(f"  {ticker} 매수 미체결 (장 마감) → buy_failed")

                elif status == "holding":
                    prev_qty = record.get("holding_quantity") or 0
                    if kis_qty > 0 and kis_qty != prev_qty:
                        supabase.table(TABLE_TRADE_RECORDS).update({"holding_quantity": kis_qty}).eq("id", record_id).execute()
                        logger.info(f"  {ticker} 보유수량 동기화: {prev_qty}주 → {kis_qty}주")

                elif status == "sell_ordered":
                    if kis_qty == 0:
                        supabase.table(TABLE_TRADE_RECORDS).update({
                            "status": "sold", "holding_quantity": 0,
                        }).eq("id", record_id).execute()
                        logger.info(f"  {ticker} 매도 체결 확인 → sold")
                        try:
                            sold_qty = record.get("quantity") or record.get("holding_quantity") or 0
                            notify_sell_filled(
                                ticker=ticker, stock_name=record.get("stock_name", ticker),
                                qty=sold_qty, fill_price=float(record.get("sell_price") or 0),
                                sell_reason=record.get("sell_reason") or "?",
                                profit_loss=float(record.get("profit_loss") or 0),
                                profit_loss_pct=float(record.get("profit_loss_pct") or 0),
                                buy_price=record.get("buy_price"), buy_date=record.get("buy_date"),
                            )
                        except Exception as notify_e:
                            logger.warning(f"  {ticker} 매도 체결 알림 발송 실패: {notify_e}")
                    elif is_after_market_close:
                        prev_holding = record.get("holding_quantity") or record.get("quantity", 0)
                        supabase.table(TABLE_TRADE_RECORDS).update({
                            "status": "holding", "holding_quantity": kis_qty,
                            "sell_price": None, "sell_date": None, "sell_reason": None,
                            "profit_loss": None, "profit_loss_pct": None,
                        }).eq("id", record_id).execute()
                        if kis_qty < prev_holding:
                            logger.warning(f"  {ticker} 부분 매도 (보유 {prev_holding}→{kis_qty}주) → holding 복원")
                        else:
                            logger.warning(f"  {ticker} 매도 미체결 (장 마감) → holding 복원")

            # 고아 감지
            for ticker, info in kis_holdings.items():
                if ticker not in tracked_tickers:
                    item = info["item"]
                    qty = info["qty"]
                    supabase.table(TABLE_TRADE_RECORDS).insert({
                        "ticker": ticker,
                        "stock_name": item.get("prdt_name", ticker),
                        "buy_price": float(item.get("pchs_avg_pric", 0) or 0),
                        "buy_date": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
                        "quantity": qty,
                        "holding_quantity": qty,
                        "status": "holding",
                        "account_type": current_account_type(),
                    }).execute()
                    logger.warning(f"  {ticker} 고아 감지: KIS 보유({qty}주) but trade_records 없음 → 자동 생성")
        except Exception as e:
            logger.error(f"주문 정합성 확인 실패: {e}", exc_info=True)

    # ── 자동 매도 ───────────────────────────────────────────────

    async def _execute_auto_sell(self):
        now_kst = datetime.now(KST)
        if now_kst.weekday() > 4:
            return

        balance_result = get_all_balances()
        self._reconcile_orders(balance_result=balance_result)

        if not _is_market_open(now_kst):
            return

        logger.info(f"한국 장 시간 확인: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")

        sell_candidates_result = self.recommendation_service.get_stocks_to_sell(balance_result=balance_result)
        if not sell_candidates_result or not sell_candidates_result.get("sell_candidates"):
            logger.info("매도 대상 종목이 없습니다.")
            return
        sell_candidates = sell_candidates_result.get("sell_candidates", [])

        # 중복 매도 방지
        try:
            sell_ordered_response = supabase.table(TABLE_TRADE_RECORDS).select("ticker").eq("status", "sell_ordered").eq("account_type", current_account_type()).execute()
            sell_ordered_tickers = {rec["ticker"] for rec in (sell_ordered_response.data or [])}
            if sell_ordered_tickers:
                sell_candidates = [c for c in sell_candidates if c["ticker"] not in sell_ordered_tickers]
        except Exception:
            pass

        if not sell_candidates:
            logger.info("매도 대상 종목이 없습니다.")
            return

        logger.info(f"매도 대상 종목 {len(sell_candidates)}개를 찾았습니다.")

        for candidate in sell_candidates:
            try:
                ticker = candidate["ticker"]
                stock_name = candidate["stock_name"]
                quantity = candidate["quantity"]

                reasons_str = "; ".join(candidate.get("sell_reasons", []))
                logger.info(f"{stock_name}({ticker}) 매도 근거: {reasons_str}")

                current_price = _current_price(ticker)
                if current_price <= 0:
                    await asyncio.sleep(1)
                    continue
                current_price = round_to_tick(current_price)

                await asyncio.sleep(0.5)
                order_data = {
                    "PDNO": ticker,
                    "ORD_DVSN": "00",  # 지정가
                    "ORD_QTY": str(quantity),
                    "ORD_UNPR": str(current_price),
                    "is_buy": False,
                }
                logger.info(f"{stock_name}({ticker}) 매도 주문: 수량 {quantity}주, 가격 {current_price:,}원")
                order_result = order_domestic_stock(order_data)

                if order_result.get("rt_cd") == "0":
                    logger.info(f"{stock_name}({ticker}) 매도 주문 성공: {order_result.get('msg1', '')}")
                    try:
                        sell_reasons = candidate.get("sell_reasons", [])
                        sell_reason = "signal"
                        for reason in sell_reasons:
                            if "익절" in reason:
                                sell_reason = "take_profit"
                                break
                            elif "손절" in reason:
                                sell_reason = "stop_loss"
                                break
                        purchase_price = candidate.get("purchase_price", 0)
                        profit_loss = (current_price - purchase_price) * quantity if purchase_price > 0 else None
                        profit_loss_pct = ((current_price - purchase_price) / purchase_price) * 100 if purchase_price > 0 else None

                        supabase.table(TABLE_TRADE_RECORDS).update({
                            "status": "sell_ordered",
                            "sell_price": current_price,
                            "sell_date": datetime.now(KST).isoformat(),
                            "sell_reason": sell_reason,
                            "profit_loss": round(profit_loss) if profit_loss is not None else None,
                            "profit_loss_pct": round(profit_loss_pct, 2) if profit_loss_pct is not None else None,
                        }).eq("ticker", ticker).eq("status", "holding").eq("account_type", current_account_type()).execute()

                        try:
                            notify_sell_ordered(ticker=ticker, stock_name=stock_name, qty=quantity, price=current_price, sell_reason=sell_reason)
                        except Exception as notify_e:
                            logger.warning(f"매도 주문 접수 알림 발송 실패: {notify_e}")
                    except Exception as tr_e:
                        logger.error(f"  {stock_name}({ticker}) trade_records 업데이트 실패: {tr_e}")
                else:
                    logger.error(f"{stock_name}({ticker}) 매도 주문 실패: {order_result.get('msg1', '')}")

                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"{candidate['stock_name']}({candidate['ticker']}) 매도 처리 중 오류: {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.info("자동 매도 처리가 완료되었습니다.")

    # ── 자동 매수 ───────────────────────────────────────────────

    async def _execute_auto_buy(self, force: bool = False):
        now_kst = datetime.now(KST)
        kst_date = now_kst.date()

        if not force:
            is_weekday = 0 <= now_kst.weekday() <= 4
            cur = (now_kst.hour, now_kst.minute)
            is_buy_time = BUY_WINDOW_START <= cur < BUY_WINDOW_END
            if not (is_weekday and is_buy_time):
                return
            if self._last_buy_date == kst_date:
                return

        logger.info(f"자동 매수 작업 시작 (force={force}, KST: {now_kst.strftime('%Y-%m-%d %H:%M:%S')})")
        any_buy_succeeded = False

        # 보유 종목 조회
        try:
            balance_result = get_all_balances()
            if balance_result.get("rt_cd") != "0":
                logger.error(f"보유 종목 조회 실패: {balance_result.get('msg1', '')}")
                return
            holdings = balance_result.get("output1", [])
            holding_tickers = set()
            initial_holdings_value = 0.0
            for item in holdings:
                ticker = item.get("pdno")
                if ticker:
                    holding_tickers.add(ticker)
                try:
                    initial_holdings_value += float(item.get("evlu_amt", 0) or 0)
                except (ValueError, TypeError):
                    pass

            try:
                ordered_response = supabase.table(TABLE_TRADE_RECORDS).select("ticker").in_(
                    "status", ["buy_ordered", "holding", "sell_ordered"]
                ).eq("account_type", current_account_type()).execute()
                if ordered_response.data:
                    for rec in ordered_response.data:
                        holding_tickers.add(rec["ticker"])
            except Exception:
                pass
            logger.info(f"현재 보유/주문 중인 종목 수: {len(holding_tickers)}")
        except Exception as e:
            logger.error(f"보유 종목 조회 중 오류 발생: {e}", exc_info=True)
            return

        recommendations = self.recommendation_service.get_combined_recommendations_with_technical_and_sentiment()
        buy_candidates = recommendations.get("results", []) if recommendations else []

        if not buy_candidates:
            logger.info("매수 대상 종목이 없습니다.")
            try:
                notify_llm_decisions(buy_candidates=[], held_candidates=[], market_analysis="")
            except Exception as notify_e:
                logger.warning(f"LLM '후보 없음' 알림 발송 실패: {notify_e}")
            return

        logger.info(f"매수 후보 {len(buy_candidates)}개 → LLM 최종 검토 시작")
        vix_value = buy_candidates[0].get("vix_value") if buy_candidates else None
        review_result = review_buy_candidates(buy_candidates, vix_value)

        try:
            notify_llm_decisions(
                buy_candidates=review_result["reviewed_candidates"],
                held_candidates=review_result["held_candidates"],
                market_analysis=review_result.get("llm_reasoning", ""),
            )
        except Exception as notify_e:
            logger.warning(f"LLM 결정 알림 발송 실패: {notify_e}")

        buy_candidates = review_result["reviewed_candidates"]
        if not buy_candidates:
            logger.info("LLM 검토 결과 매수 대상이 없습니다.")
            return

        logger.info(f"LLM 검토 통과: {len(buy_candidates)}개 종목 매수 진행")

        for candidate in buy_candidates:
            try:
                ticker = candidate["ticker"]
                stock_name = candidate["stock_name"]

                if ticker in holding_tickers:
                    logger.info(f"{stock_name}({ticker}) - 이미 보유 중 → 매수 안 함.")
                    continue

                current_price = _current_price(ticker)
                if current_price <= 0:
                    await asyncio.sleep(1)
                    continue
                current_price = round_to_tick(current_price)

                await asyncio.sleep(0.5)
                # 매수가능금액 조회 → 총자산 기준 종목당 N% 투자
                try:
                    ps_result = inquire_psbl_amount(ticker, str(current_price), ord_dvsn="00")
                    if ps_result.get("rt_cd") != "0":
                        logger.error(f"{stock_name}({ticker}) 매수가능금액 조회 실패: {ps_result.get('msg1', '')}")
                        continue
                    ps_output = ps_result.get("output", {})
                    available_amount = float(ps_output.get("ord_psbl_cash", 0) or ps_output.get("nrcvb_buy_amt", 0) or 0)
                    if available_amount <= 0:
                        logger.info(f"{stock_name}({ticker}) 매수가능금액이 없습니다.")
                        continue

                    total_assets = available_amount + initial_holdings_value
                    invest_amount = total_assets * settings.KOR_INVEST_RATIO
                    if invest_amount > available_amount:
                        logger.warning(f"{stock_name}({ticker}) 가용현금 부족: 슬롯 {invest_amount:,.0f} > 현금 {available_amount:,.0f} → 가용현금 한도로 조정")
                        invest_amount = available_amount

                    quantity = int(invest_amount / current_price)
                    if quantity < 1:
                        logger.info(f"{stock_name}({ticker}) 투자금({invest_amount:,.0f}원)으로 1주도 살 수 없습니다. (현재가 {current_price:,}원)")
                        continue

                    logger.info(
                        f"{stock_name}({ticker}) 총자산: {total_assets:,.0f}원 "
                        f"(현금 {available_amount:,.0f} + 보유평가 {initial_holdings_value:,.0f}), "
                        f"슬롯({settings.KOR_INVEST_RATIO*100:.0f}%): {invest_amount:,.0f}원, 수량: {quantity}주"
                    )
                except Exception as ps_e:
                    logger.error(f"{stock_name}({ticker}) 매수가능금액 조회 오류: {ps_e}")
                    continue

                # ATR 계산 (실패 시 매수 SKIP)
                await asyncio.sleep(0.5)
                atr_value = None
                try:
                    vol_result = get_daily_price(ticker)
                    if vol_result and vol_result.get("rt_cd") == "0":
                        atr_value = self.recommendation_service.calculate_atr(vol_result.get("output2", []))
                except Exception as atr_e:
                    logger.error(f"{stock_name}({ticker}) ATR 계산 중 오류: {atr_e}")

                if atr_value is None:
                    logger.warning(f"❌ {stock_name}({ticker}) ATR 계산 실패 → 매수 SKIP (자동 익절/손절 안전장치 없이 매수 안 함)")
                    await asyncio.sleep(0.5)
                    continue

                take_profit_price = round_to_tick(current_price + atr_value * 2.5)
                stop_loss_price = round_to_tick(current_price - atr_value * 1.5)
                logger.info(f"  ATR={atr_value}, 익절가={take_profit_price:,}원, 손절가={stop_loss_price:,}원")

                await asyncio.sleep(0.5)
                order_data = {
                    "PDNO": ticker,
                    "ORD_DVSN": "00",  # 지정가
                    "ORD_QTY": str(quantity),
                    "ORD_UNPR": str(current_price),
                    "is_buy": True,
                }
                logger.info(f"{stock_name}({ticker}) 매수 주문: 수량 {quantity}주, 가격 {current_price:,}원")
                order_result = order_domestic_stock(order_data)

                if order_result.get("rt_cd") == "0":
                    logger.info(f"{stock_name}({ticker}) 매수 주문 성공: {order_result.get('msg1', '')}")
                    holding_tickers.add(ticker)
                    any_buy_succeeded = True
                    try:
                        notify_buy_ordered(ticker=ticker, stock_name=stock_name, qty=quantity, price=current_price, composite_score=candidate.get("composite_score", 0))
                    except Exception as notify_e:
                        logger.warning(f"매수 주문 접수 알림 발송 실패: {notify_e}")
                    try:
                        supabase.table(TABLE_TRADE_RECORDS).insert({
                            "ticker": ticker,
                            "stock_name": stock_name,
                            "buy_price": current_price,
                            "buy_date": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                            "quantity": quantity,
                            "holding_quantity": 0,
                            "atr": atr_value,
                            "take_profit_price": take_profit_price,
                            "stop_loss_price": stop_loss_price,
                            "status": "buy_ordered",
                            "composite_score": candidate.get("composite_score"),
                            "account_type": current_account_type(),
                        }).execute()
                        logger.info(f"  {stock_name}({ticker}) trade_records 저장 완료 (status: buy_ordered)")
                    except Exception as tr_e:
                        logger.error(f"  {stock_name}({ticker}) trade_records 저장 실패: {tr_e}")
                else:
                    logger.error(f"{stock_name}({ticker}) 매수 주문 실패: {order_result.get('msg1', '')}")

                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"{candidate['stock_name']}({candidate['ticker']}) 매수 처리 중 오류: {e}", exc_info=True)

        if any_buy_succeeded:
            self._last_buy_date = kst_date
            logger.info(f"자동 매수 처리 완료. (_last_buy_date={kst_date})")
        else:
            logger.info("자동 매수 처리 완료. (성공 매수 없음 → _last_buy_date 갱신 안 함)")


# 싱글톤 인스턴스
stock_scheduler = StockScheduler()


def start_scheduler():
    return stock_scheduler.start()


def stop_scheduler():
    return stock_scheduler.stop()


def start_sell_scheduler():
    return stock_scheduler.start_sell_scheduler()


def stop_sell_scheduler():
    return stock_scheduler.stop_sell_scheduler()


def get_scheduler_status():
    return {"buy_running": stock_scheduler.running, "sell_running": stock_scheduler.sell_running}


def run_auto_buy_now():
    def _runner():
        try:
            asyncio.run(stock_scheduler._execute_auto_buy(force=True))
        except Exception as e:
            logger.error(f"수동 매수 실행 중 오류: {e}", exc_info=True)
    threading.Thread(target=_runner, daemon=True).start()
    return True


def run_auto_sell_now():
    def _runner():
        try:
            asyncio.run(stock_scheduler._execute_auto_sell())
        except Exception as e:
            logger.error(f"수동 매도 실행 중 오류: {e}", exc_info=True)
    threading.Thread(target=_runner, daemon=True).start()
    return True


# ── 경제 데이터 스케줄러 ────────────────────────────────────────

economic_data_scheduler_running = False


def _run_economic_data_update(force: bool = False):
    try:
        elog = logging.getLogger('economic_scheduler_kor')
        elog.info("경제 데이터 업데이트 작업 시작")
        asyncio.run(update_economic_data_in_background(force=force))
        elog.info("경제 데이터 업데이트 작업 완료")
        return True
    except Exception as e:
        logging.getLogger('economic_scheduler_kor').error(f"경제 데이터 업데이트 중 오류: {e}", exc_info=True)
        return False


def start_economic_data_scheduler():
    global economic_data_scheduler_running
    if economic_data_scheduler_running:
        return False
    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_economic_data_update']:
        schedule.cancel_job(job)
    schedule.every().day.at("16:30").do(_run_economic_data_update)  # 장 마감(15:30) 후
    economic_data_scheduler_running = True
    logging.getLogger('economic_scheduler_kor').info("경제 데이터 스케줄러 시작 (매일 16:30 KST)")
    return True


def stop_economic_data_scheduler():
    global economic_data_scheduler_running
    if not economic_data_scheduler_running:
        return False
    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_economic_data_update']:
        schedule.cancel_job(job)
    economic_data_scheduler_running = False
    logging.getLogger('economic_scheduler_kor').info("경제 데이터 스케줄러 중지됨.")
    return True


def run_economic_data_update_now(force: bool = False):
    return _run_economic_data_update(force=force)


# ══════════════════════════════════════════════════════════════════
# 일일 통합 파이프라인 (KST 18:00)
#   Step 1) 경제 데이터 수집  →  2) Kaggle ML  →  3) 기술+감성  →  4) LLM+매수
# ══════════════════════════════════════════════════════════════════

daily_pipeline_scheduler_running = False


async def _execute_daily_pipeline() -> dict:
    pipeline_logger = logging.getLogger('daily_pipeline_kor')
    pipeline_logger.info("===== KR Daily Pipeline 시작 =====")
    pipeline_start = time.time()
    completed_steps: dict = {}

    def _fail(step_key: str, step_name: str, error: str) -> dict:
        try:
            notify_pipeline_failure(failed_step=step_key, step_name=step_name, error=error, completed_steps=completed_steps)
        except Exception as notify_e:
            pipeline_logger.warning(f"Pipeline 실패 알림 발송 실패: {notify_e}")
        return {
            "success": False, "failed_step": step_key, "step_name": step_name, "error": error,
            "completed_steps": completed_steps, "total_elapsed_sec": int(time.time() - pipeline_start),
        }

    # Step 1
    step_name, step_key = "경제 데이터 + 주가 수집", "1_economic_data"
    pipeline_logger.info(f"[1/4] {step_name} 시작")
    step_start = time.time()
    try:
        await update_economic_data_in_background(force=True)
        completed_steps[step_key] = {"step_name": step_name, "elapsed_sec": int(time.time() - step_start)}
        pipeline_logger.info(f"[1/4] {step_name} 완료")
    except Exception as e:
        pipeline_logger.error(f"[1/4] {step_name} 실패: {e}", exc_info=True)
        return _fail(step_key, step_name, str(e))

    # Step 2
    step_name, step_key = "Kaggle ML 예측", "2_kaggle_ml"
    pipeline_logger.info(f"[2/4] {step_name} 시작")
    step_start = time.time()
    try:
        success, msg, meta = trigger_and_wait()
        if not success:
            raise RuntimeError(f"Kaggle 실행 실패: {msg} (meta={meta})")
        completed_steps[step_key] = {"step_name": step_name, "elapsed_sec": int(time.time() - step_start)}
        pipeline_logger.info(f"[2/4] {step_name} 완료")
    except Exception as e:
        pipeline_logger.error(f"[2/4] {step_name} 실패: {e}", exc_info=True)
        return _fail(step_key, step_name, str(e))

    # Step 3
    step_name, step_key = "기술 지표 + 뉴스 감성 분석", "3_technical_sentiment"
    pipeline_logger.info(f"[3/4] {step_name} 시작")
    step_start = time.time()
    try:
        service = StockRecommendationService()
        service.generate_technical_recommendations()
        service.fetch_and_store_sentiment_for_recommendations()
        completed_steps[step_key] = {"step_name": step_name, "elapsed_sec": int(time.time() - step_start)}
        pipeline_logger.info(f"[3/4] {step_name} 완료")
    except Exception as e:
        pipeline_logger.error(f"[3/4] {step_name} 실패: {e}", exc_info=True)
        return _fail(step_key, step_name, str(e))

    try:
        data_total = int(time.time() - pipeline_start)
        notify_data_ready(
            elapsed_sec=data_total,
            steps_summary={
                "1_economic": completed_steps["1_economic_data"]["elapsed_sec"],
                "2_kaggle": completed_steps["2_kaggle_ml"]["elapsed_sec"],
                "3_tech_sent": completed_steps["3_technical_sentiment"]["elapsed_sec"],
            },
        )
    except Exception as notify_e:
        pipeline_logger.warning(f"데이터 수집 완료 알림 발송 실패: {notify_e}")

    # Step 4
    step_name, step_key = "LLM 최종 검토 + KIS 매수 주문", "4_llm_buy"
    pipeline_logger.info(f"[4/4] {step_name} 시작")
    step_start = time.time()
    try:
        await stock_scheduler._execute_auto_buy(force=True)
        completed_steps[step_key] = {"step_name": step_name, "elapsed_sec": int(time.time() - step_start)}
        pipeline_logger.info(f"[4/4] {step_name} 완료")
    except Exception as e:
        pipeline_logger.error(f"[4/4] {step_name} 실패: {e}", exc_info=True)
        return _fail(step_key, step_name, str(e))

    total_elapsed = int(time.time() - pipeline_start)
    pipeline_logger.info(f"===== KR Daily Pipeline 완료 (총 {total_elapsed}초) =====")
    return {
        "success": True, "failed_step": None, "step_name": None, "error": None,
        "completed_steps": completed_steps, "total_elapsed_sec": total_elapsed,
    }


def _run_daily_pipeline():
    pipeline_logger = logging.getLogger('daily_pipeline_kor')
    kst_weekday = datetime.now(KST).weekday()
    if kst_weekday >= 5:
        pipeline_logger.info(f"주말(KST weekday={kst_weekday}) — 일일 파이프라인 스킵")
        return True
    try:
        result = asyncio.run(_execute_daily_pipeline())
        if not result["success"]:
            pipeline_logger.error(f"KR Daily Pipeline 중단 — 실패 단계: {result['failed_step']}, 사유: {result['error']}")
        return result["success"]
    except Exception as e:
        pipeline_logger.error(f"KR Daily Pipeline 실행 중 예외: {e}", exc_info=True)
        return False


def start_daily_pipeline_scheduler():
    global daily_pipeline_scheduler_running
    pipeline_logger = logging.getLogger('daily_pipeline_kor')
    if daily_pipeline_scheduler_running:
        return False
    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_daily_pipeline']:
        schedule.cancel_job(job)
    schedule.every().day.at("18:00").do(_run_daily_pipeline)
    daily_pipeline_scheduler_running = True
    pipeline_logger.info("일일 파이프라인 스케줄러 시작 (매일 KST 18:00)")
    return True


def stop_daily_pipeline_scheduler():
    global daily_pipeline_scheduler_running
    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_daily_pipeline']:
        schedule.cancel_job(job)
    daily_pipeline_scheduler_running = False
    logging.getLogger('daily_pipeline_kor').info("일일 파이프라인 스케줄러 중지됨.")
    return True


def run_daily_pipeline_now():
    return _run_daily_pipeline()

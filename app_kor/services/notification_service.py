"""
Slack Incoming Webhook 통합 (한국주식판) — 6가지 알림 + 보유 현황표.

미국판과 동일한 함수 시그니처를 유지하되, 통화를 원(₩)으로, 잔고 필드를
국내주식(pdno/hldg_qty/prpr/...) 으로, 장 시간을 KST 09:00~15:30 으로 치환.

SLACK_WEBHOOK_URL 미설정 시 모든 함수가 no-op (안전).
"""
import logging
import requests
from typing import List, Optional, Dict

from app_kor.core.config import settings
from app_kor.core.constants import TABLE_TRADE_RECORDS

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# 저수준 전송
# ──────────────────────────────────────────────────────────

def _send(title: str, message: str, color: str = "#36a64f", fields: Optional[Dict[str, str]] = None) -> bool:
    if not settings.SLACK_WEBHOOK_URL:
        return False
    attachment = {"color": color, "title": title, "text": message, "mrkdwn_in": ["text", "fields"]}
    if fields:
        attachment["fields"] = [{"title": k, "value": str(v), "short": True} for k, v in fields.items()]
    try:
        resp = requests.post(settings.SLACK_WEBHOOK_URL, json={"attachments": [attachment]}, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Slack 전송 실패 ({resp.status_code}): {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Slack 전송 예외: {e}")
        return False


# ──────────────────────────────────────────────────────────
# 보유 종목 + 수익률 현황표
# ──────────────────────────────────────────────────────────

def format_holdings_table() -> str:
    """KIS 국내주식 잔고에서 보유 종목 + 수익률을 모노스페이스 표로 반환."""
    try:
        from app_kor.services.balance_service import get_all_balances
        balance = get_all_balances()
    except Exception as e:
        return f"_(보유 종목 조회 실패: {e})_"

    if balance.get("rt_cd") != "0":
        return f"_(보유 종목 조회 실패: {balance.get('msg1', '')})_"

    holdings = balance.get("output1", [])
    if not holdings:
        return "_(현재 보유 종목 없음)_"

    lines = ["```"]
    lines.append(f"{'종목':<12} {'수량':>5} {'평단가':>10} {'현재가':>10} {'손익(원)':>14} {'수익률':>8}")
    lines.append("─" * 64)

    total_pnl = 0.0
    total_buy = 0.0
    for h in holdings:
        ticker = h.get("pdno", "")
        name = h.get("prdt_name", "")
        if len(name) > 8:
            name = name[:8]
        try:
            qty = int(h.get("hldg_qty", 0) or 0)
            buy_price = float(h.get("pchs_avg_pric", 0) or 0)
            now_price = float(h.get("prpr", 0) or 0)
            pnl = float(h.get("evlu_pfls_amt", 0) or 0)
            pnl_pct = float(h.get("evlu_pfls_rt", 0) or 0)
        except (ValueError, TypeError):
            continue
        total_pnl += pnl
        total_buy += buy_price * qty
        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"{name:<8}({ticker:<6}) {qty:>5} "
            f"{buy_price:>9,.0f} {now_price:>9,.0f} "
            f"{sign}{pnl:>11,.0f} {sign}{pnl_pct:>6.2f}%"
        )

    lines.append("─" * 64)
    total_sign = "+" if total_pnl >= 0 else ""
    total_pct = (total_pnl / total_buy * 100) if total_buy > 0 else 0
    lines.append(f"{'합계':<20} {total_buy:>10,.0f}원 → {total_sign}{total_pnl:,.0f}원 ({total_sign}{total_pct:.2f}%)")
    lines.append("```")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# ① 데이터 수집 완료
# ──────────────────────────────────────────────────────────

def notify_data_ready(elapsed_sec: int, steps_summary: dict):
    _send(
        title="📥 데이터 수집 완료 (KR)",
        message=f"오늘 매수 판단을 위한 모든 데이터가 갱신됐습니다. ({elapsed_sec}초)",
        color="#2eb886",
        fields={
            "Step 1 경제데이터": f"{steps_summary.get('1_economic', '?')}초",
            "Step 2 Kaggle ML": f"{steps_summary.get('2_kaggle', '?')}초",
            "Step 3 기술+감성": f"{steps_summary.get('3_tech_sent', '?')}초",
        },
    )


# ──────────────────────────────────────────────────────────
# ② 오늘 매수 / 홀드 결정
# ──────────────────────────────────────────────────────────

def notify_llm_decisions(buy_candidates: List[dict], held_candidates: List[dict], market_analysis: str = ""):
    if not buy_candidates and not held_candidates:
        _send(title="📋 오늘 매수 후보 없음 (KR)", message="기술/감성/ML 필터를 통과한 종목이 없습니다.", color="#888888")
        return

    buy_lines = [
        f"• *{c.get('stock_name')}* ({c.get('ticker')}) score={c.get('composite_score', 0):.3f} "
        f"rise={c.get('rise_probability', 0):.2f}% — _{c.get('llm_reason', '')}_"
        for c in buy_candidates
    ]
    hold_lines = [
        f"• {c.get('stock_name')} ({c.get('ticker')}) score={c.get('composite_score', 0):.3f} — _{c.get('llm_reason', '')}_"
        for c in held_candidates
    ]

    body_parts = []
    if market_analysis:
        body_parts.append(f"💬 *시장 분석:* {market_analysis}\n")
    if buy_lines:
        body_parts.append(f"🟢 *BUY ({len(buy_lines)}건)*\n" + "\n".join(buy_lines))
    if hold_lines:
        body_parts.append(f"🟡 *HOLD ({len(hold_lines)}건)*\n" + "\n".join(hold_lines))

    _send(
        title=f"📋 오늘 결정 (KR) — BUY {len(buy_candidates)} / HOLD {len(held_candidates)}",
        message="\n\n".join(body_parts),
        color="#3b82f6",
    )


# ──────────────────────────────────────────────────────────
# ③ 매수
# ──────────────────────────────────────────────────────────

def notify_buy_ordered(ticker: str, stock_name: str, qty: int, price: float, composite_score: float):
    _send(
        title=f"📋 매수 주문 접수: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *주문가(지정가):* {price:,.0f}원  *예상총액:* {qty * price:,.0f}원\n"
            f"*composite_score:* {composite_score:.4f}\n"
            f"_⏳ 거래소 체결은 정규 시간(KST 09:00~15:30) 매칭 후 별도 '체결' 알림 발송_"
        ),
        color="#3b82f6",
    )


def _get_account_summary() -> str:
    """KIS 국내주식 잔고 output2 에서 계좌 요약."""
    try:
        from app_kor.services.balance_service import get_all_balances
        balance = get_all_balances()
        if balance.get("rt_cd") != "0":
            return ""
        output2 = balance.get("output2") or []
        if not output2:
            return ""
        summary = output2[0]
        tot_evlu = float(summary.get("tot_evlu_amt", 0) or 0)
        pchs_total = float(summary.get("pchs_amt_smtl_amt", 0) or 0)
        pnl_total = float(summary.get("evlu_pfls_smtl_amt", 0) or 0)
        dnca = float(summary.get("dnca_tot_amt", 0) or 0)
        if pchs_total <= 0 and tot_evlu <= 0:
            return ""
        pnl_pct = (pnl_total / pchs_total * 100) if pchs_total > 0 else 0
        sign = "+" if pnl_total >= 0 else ""
        return (
            f"*💼 현재 계좌 ({'모의' if settings.KIS_USE_MOCK else '실거래'})*\n"
            f"  총 평가액:   {tot_evlu:,.0f}원\n"
            f"  예수금:      {dnca:,.0f}원\n"
            f"  매입 원금:   {pchs_total:,.0f}원\n"
            f"  평가 손익:   {sign}{pnl_total:,.0f}원 ({sign}{pnl_pct:.2f}%)\n"
        )
    except Exception as e:
        logger.warning(f"계좌 요약 조회 실패: {e}")
        return ""


def _get_today_trade_summary() -> str:
    """오늘(KST) 매수/매도 거래 통계."""
    try:
        from app_kor.db.supabase import supabase
        from datetime import datetime
        import pytz
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        today_kst = now_kst.strftime("%Y-%m-%d")
        utc_start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
        utc_start_str = utc_start.strftime("%Y-%m-%dT%H:%M:%S")

        res = supabase.table(TABLE_TRADE_RECORDS).select(
            "id, ticker, status, buy_price, quantity, sell_price, profit_loss"
        ).gte("created_at", utc_start_str).execute()
        rows = res.data or []
        buy_count = buy_amount = sell_count = realized_pnl = 0
        buy_amount = 0.0
        realized_pnl = 0.0
        for r in rows:
            if r.get("status") in ("buy_ordered", "holding"):
                buy_count += 1
                buy_amount += float(r.get("buy_price") or 0) * (r.get("quantity") or 0)
            elif r.get("status") == "sold":
                sell_count += 1
                realized_pnl += float(r.get("profit_loss") or 0)
        if buy_count == 0 and sell_count == 0:
            return ""
        sign = "+" if realized_pnl >= 0 else ""
        return (
            f"*📊 오늘 거래 요약 ({today_kst})*\n"
            f"  매수: {buy_count}건 / {buy_amount:,.0f}원\n"
            f"  매도: {sell_count}건 / 실현손익 {sign}{realized_pnl:,.0f}원\n"
        )
    except Exception as e:
        logger.warning(f"오늘 거래 요약 조회 실패: {e}")
        return ""


def _calc_holding_days(buy_date_str: Optional[str]) -> Optional[int]:
    if not buy_date_str:
        return None
    try:
        from datetime import datetime
        import pytz
        buy_dt = datetime.strptime(buy_date_str[:10], "%Y-%m-%d")
        now_kst = datetime.now(pytz.timezone('Asia/Seoul')).replace(tzinfo=None)
        return max((now_kst.date() - buy_dt.date()).days, 0)
    except Exception:
        return None


def notify_buy_filled(ticker: str, stock_name: str, qty: int, fill_price: float,
                      take_profit_price: Optional[float] = None, stop_loss_price: Optional[float] = None,
                      composite_score: Optional[float] = None):
    total_amount = qty * fill_price
    parts = [f"*이번 거래*", f"  {qty}주 @ {fill_price:,.0f}원 = *{total_amount:,.0f}원*"]
    if composite_score is not None:
        parts.append(f"  종합점수: {composite_score:.4f} (LLM BUY)")

    if take_profit_price and stop_loss_price and fill_price > 0:
        tp_pct = (take_profit_price - fill_price) / fill_price * 100
        sl_pct = (stop_loss_price - fill_price) / fill_price * 100
        rr = tp_pct / abs(sl_pct) if sl_pct != 0 else 0
        parts.append("")
        parts.append(f"*🎯 자동 청산 라인 (ATR 기반)*")
        parts.append(f"  익절가: {take_profit_price:,.0f}원 (+{tp_pct:.2f}%)  ← 도달 시 자동 매도")
        parts.append(f"  손절가: {stop_loss_price:,.0f}원 ({sl_pct:.2f}%)  ← 도달 시 자동 손절")
        parts.append(f"  보상/위험: {rr:.2f} : 1")

    today_summary = _get_today_trade_summary()
    if today_summary:
        parts.append("")
        parts.append(today_summary.rstrip())
    account_summary = _get_account_summary()
    if account_summary:
        parts.append("")
        parts.append(account_summary.rstrip())
    parts.append("")
    parts.append(f"*📊 현재 보유 종목*")
    parts.append(format_holdings_table())

    _send(title=f"✅ 매수 체결: {stock_name} ({ticker})", message="\n".join(parts), color="#36a64f")


# ──────────────────────────────────────────────────────────
# ④ 매도
# ──────────────────────────────────────────────────────────

def notify_sell_ordered(ticker: str, stock_name: str, qty: int, price: float, sell_reason: str):
    _send(
        title=f"📋 매도 주문 접수: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *주문가(지정가):* {price:,.0f}원  *사유:* `{sell_reason}`\n"
            f"_⏳ 거래소 체결은 정규 시간(KST 09:00~15:30) 매칭 후 별도 '체결' 알림 발송_"
        ),
        color="#3b82f6",
    )


def notify_sell_filled(ticker: str, stock_name: str, qty: int, fill_price: float, sell_reason: str,
                       profit_loss: float, profit_loss_pct: float,
                       buy_price: Optional[float] = None, buy_date: Optional[str] = None):
    is_profit = profit_loss >= 0
    icon = "💰" if is_profit else "🩸"
    color = "#2eb886" if is_profit else "#ff9800"
    sign = "+" if is_profit else ""

    reason_kr = {
        "trailing_stop": "트레일링 스톱 청산",
        "take_profit": "익절 (목표가 도달)",
        "stop_loss": "손절 (손실 한도 도달)",
        "signal": "기술 신호 매도",
        "panic_sell": "패닉셀 (당일 급락+거래량 폭증)",
    }.get(sell_reason, sell_reason)

    parts = [f"*이번 거래*", f"  {qty}주 @ {fill_price:,.0f}원"]
    if buy_price and buy_price > 0:
        parts.append(f"  매수가 {buy_price:,.0f}원 → 매도가 {fill_price:,.0f}원")
    parts.append(f"  손익: *{sign}{profit_loss:,.0f}원* ({sign}{profit_loss_pct:.2f}%)")
    parts.append(f"  사유: `{reason_kr}`")
    holding_days = _calc_holding_days(buy_date)
    if holding_days is not None:
        parts.append(f"  보유 기간: {holding_days}일")

    today_summary = _get_today_trade_summary()
    if today_summary:
        parts.append("")
        parts.append(today_summary.rstrip())
    account_summary = _get_account_summary()
    if account_summary:
        parts.append("")
        parts.append(account_summary.rstrip())
    parts.append("")
    parts.append(f"*📊 매도 후 보유 종목*")
    parts.append(format_holdings_table())

    _send(
        title=f"{icon} 매도 체결: {stock_name} ({ticker})  {sign}{profit_loss:,.0f}원 ({sign}{profit_loss_pct:.2f}%)",
        message="\n".join(parts),
        color=color,
    )


# 하위 호환 alias
notify_buy_executed = notify_buy_ordered
notify_sell_executed = notify_sell_ordered


# ──────────────────────────────────────────────────────────
# ⑤ 일일 파이프라인 실패
# ──────────────────────────────────────────────────────────

def notify_pipeline_failure(failed_step: str, step_name: str, error: str,
                            completed_steps: Optional[Dict[str, dict]] = None):
    fields = {"❌ 실패 단계": f"{step_name}\n({failed_step})"}
    if completed_steps:
        for k, v in completed_steps.items():
            fields[f"✅ {v.get('step_name', k)}"] = f"{v.get('elapsed_sec', '?')}초"
    _send(
        title=f"❌ Pipeline 실패 (KR) — {step_name}",
        message=(
            f"한국주식 일일 자동매매 파이프라인이 *{step_name}* 단계에서 실패했습니다.\n"
            f"이번 사이클의 매수는 진행되지 않습니다.\n\n*에러:*\n```{(error or '')[:500]}```"
        ),
        color="#ff0000",
        fields=fields,
    )


# ──────────────────────────────────────────────────────────
# ⑥ LLM 검토 전체 실패
# ──────────────────────────────────────────────────────────

def notify_llm_failure(reason: str, candidate_count: int = 0):
    _send(
        title="❌ LLM 검토 실패 (KR) — 매수 차단",
        message=(
            f"Claude API 호출이 전체 실패했습니다 (Opus 3회 + Sonnet 3회).\n"
            f"Fail-Close 안전 정책으로 *오늘 매수 진행 안 함*.\n\n"
            f"검토 시도 후보: *{candidate_count}개*\n\n*에러:*\n```{(reason or '')[:500]}```"
        ),
        color="#ff0000",
        fields={"조치 권장": "Anthropic API 키 / 잔액 / 서비스 상태 확인"},
    )

"""
Slack Incoming Webhook 통합 — 6가지 알림 + 보유 현황표.

정상 알림 4종:
  1. notify_data_ready()           — 데이터 수집 완료 (Step 1~3)
  2. notify_llm_decisions()        — 오늘 매수/홀드 결정 종목
  3. notify_buy_executed()         — 매수 체결 (보유 현황표 첨부)
  4. notify_sell_executed()        — 매도 체결 (보유 현황표 첨부)

장애 알림 2종:
  5. notify_pipeline_failure()     — 일일 파이프라인 실패 (Step 1~4 중 어디서)
  6. notify_llm_failure()          — LLM 검토 전체 실패 (Fail-Close 매수 차단)

SLACK_WEBHOOK_URL 미설정 시 모든 함수가 no-op (안전).

참조: documents/09_Slack_연동.md
"""
import logging
import requests
from typing import List, Optional, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# 저수준 전송 함수
# ──────────────────────────────────────────────────────────

def _send(
    title: str,
    message: str,
    color: str = "#36a64f",
    fields: Optional[Dict[str, str]] = None,
) -> bool:
    """Slack Webhook 으로 attachment 1개 발송. 실패해도 본 로직 안 막음."""
    if not settings.SLACK_WEBHOOK_URL:
        return False

    attachment = {
        "color": color,
        "title": title,
        "text": message,
        "mrkdwn_in": ["text", "fields"],
    }
    if fields:
        attachment["fields"] = [
            {"title": k, "value": str(v), "short": True}
            for k, v in fields.items()
        ]

    try:
        resp = requests.post(
            settings.SLACK_WEBHOOK_URL,
            json={"attachments": [attachment]},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(f"Slack 전송 실패 ({resp.status_code}): {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Slack 전송 예외: {e}")
        return False


# ──────────────────────────────────────────────────────────
# 보유 종목 + 수익률 현황표 (매수/매도 알림에 자동 첨부)
# ──────────────────────────────────────────────────────────

def format_holdings_table() -> str:
    """
    KIS 잔고에서 보유 종목 + 수익률을 모노스페이스 표 형태로 반환.
    Slack 코드블록 마크다운으로 감싸서 정렬 유지.
    """
    try:
        from app.services.balance_service import get_all_overseas_balances
        balance = get_all_overseas_balances()
    except Exception as e:
        return f"_(보유 종목 조회 실패: {e})_"

    if balance.get("rt_cd") != "0":
        return f"_(보유 종목 조회 실패: {balance.get('msg1', '')})_"

    holdings = balance.get("output1", [])
    if not holdings:
        return "_(현재 보유 종목 없음)_"

    lines = ["```"]
    lines.append(f"{'종목':<12} {'수량':>5} {'평단가':>9} {'현재가':>9} {'손익(USD)':>12} {'수익률':>8}")
    lines.append("─" * 60)

    total_pnl_usd = 0.0
    total_buy_usd = 0.0

    for h in holdings:
        ticker = h.get("ovrs_pdno", "")
        name = h.get("ovrs_item_name", "")
        # 한글 이름은 폭이 넓으므로 잘라냄
        if len(name) > 6:
            name = name[:6]
        try:
            qty = int(h.get("ovrs_cblc_qty", 0))
            buy_price = float(h.get("pchs_avg_pric", 0))
            now_price = float(h.get("now_pric2", 0))
            pnl = float(h.get("frcr_evlu_pfls_amt", 0))
            pnl_pct = float(h.get("evlu_pfls_rt", 0))
        except (ValueError, TypeError):
            continue

        total_pnl_usd += pnl
        total_buy_usd += buy_price * qty

        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"{name:<6}({ticker:<5}) {qty:>5} "
            f"${buy_price:>7.2f} ${now_price:>7.2f} "
            f"{sign}${pnl:>9.2f} {sign}{pnl_pct:>6.2f}%"
        )

    lines.append("─" * 60)
    total_sign = "+" if total_pnl_usd >= 0 else ""
    total_pct = (total_pnl_usd / total_buy_usd * 100) if total_buy_usd > 0 else 0
    lines.append(
        f"{'합계':<20} ${total_buy_usd:>8.2f} → "
        f"{total_sign}${total_pnl_usd:>9.2f} ({total_sign}{total_pct:.2f}%)"
    )
    lines.append("```")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# ① 데이터 수집 완료
# ──────────────────────────────────────────────────────────

def notify_data_ready(elapsed_sec: int, steps_summary: dict):
    """
    Step 1~3 (경제데이터 + Kaggle ML + 기술지표+감성) 완료 직후 호출.
    """
    _send(
        title="📥 데이터 수집 완료",
        message=f"오늘 매수 판단을 위한 모든 데이터가 갱신됐습니다. ({elapsed_sec}초)",
        color="#2eb886",
        fields={
            "Step 1 경제데이터": f"{steps_summary.get('1_economic', '?')}초",
            "Step 2 Kaggle ML": f"{steps_summary.get('2_kaggle', '?')}초",
            "Step 3 기술+감성": f"{steps_summary.get('3_tech_sent', '?')}초",
        },
    )


# ──────────────────────────────────────────────────────────
# ② 오늘 매수 / 홀드 결정 종목
# ──────────────────────────────────────────────────────────

def notify_llm_decisions(
    buy_candidates: List[dict],
    held_candidates: List[dict],
    market_analysis: str = "",
):
    """
    LLM 검토 (review_buy_candidates) 직후 호출.
    Args:
        buy_candidates: BUY 판정된 종목 리스트
        held_candidates: HOLD 판정된 종목 리스트
        market_analysis: LLM 의 시장 분석 한 줄
    """
    if not buy_candidates and not held_candidates:
        _send(
            title="📋 오늘 매수 후보 없음",
            message="기술/감성/ML 필터를 통과한 종목이 없습니다.",
            color="#888888",
        )
        return

    # 매수 종목 라인
    buy_lines = []
    for c in buy_candidates:
        buy_lines.append(
            f"• *{c.get('stock_name')}* ({c.get('ticker')}) "
            f"score={c.get('composite_score', 0):.3f} "
            f"rise={c.get('rise_probability', 0):.2f}% "
            f"— _{c.get('llm_reason', '')[:80]}_"
        )

    # 홀드 종목 라인
    hold_lines = []
    for c in held_candidates:
        hold_lines.append(
            f"• {c.get('stock_name')} ({c.get('ticker')}) "
            f"score={c.get('composite_score', 0):.3f} "
            f"— _{c.get('llm_reason', '')[:80]}_"
        )

    body_parts = []
    if market_analysis:
        body_parts.append(f"💬 *시장 분석:* {market_analysis}\n")
    if buy_lines:
        body_parts.append(f"🟢 *BUY ({len(buy_lines)}건)*\n" + "\n".join(buy_lines))
    if hold_lines:
        body_parts.append(f"🟡 *HOLD ({len(hold_lines)}건)*\n" + "\n".join(hold_lines))

    _send(
        title=f"📋 오늘 결정 — BUY {len(buy_candidates)} / HOLD {len(held_candidates)}",
        message="\n\n".join(body_parts),
        color="#3b82f6",
    )


# ──────────────────────────────────────────────────────────
# ③ 매수 체결
# ──────────────────────────────────────────────────────────

def notify_buy_ordered(
    ticker: str,
    stock_name: str,
    qty: int,
    price: float,
    composite_score: float,
):
    """매수 주문 접수 직후 호출 (실 체결 X, KIS에 주문 전송 성공만 의미)."""
    _send(
        title=f"📋 매수 주문 접수: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *주문가(지정가):* ${price:.2f}  "
            f"*예상총액:* ${qty * price:,.2f}\n"
            f"*composite_score:* {composite_score:.4f}\n"
            f"_⏳ 거래소 체결은 정규 시간(NY 09:30~16:00) 매칭 후 별도 '체결' 알림 발송_"
        ),
        color="#3b82f6",  # 파란색 (정보성)
    )


def _get_account_summary() -> str:
    """KIS 잔고에서 계좌 전체 평가 정보 추출 (보유 종목 합산 방식)."""
    try:
        from app.services.balance_service import get_all_overseas_balances
        balance = get_all_overseas_balances()
        if balance.get("rt_cd") != "0":
            return ""
        holdings = balance.get("output1") or []
        if not holdings:
            return ""
        # 각 보유 종목의 외화 매입금액/평가손익을 합산
        pchs_total = 0.0  # 외화 매입금액 합계 (USD)
        pnl_total = 0.0   # 외화 평가손익 합계 (USD)
        evlu_total = 0.0  # 외화 평가금액 합계 (USD)
        for h in holdings:
            try:
                pchs_total += float(h.get("frcr_pchs_amt1", 0) or 0)
                pnl_total += float(h.get("frcr_evlu_pfls_amt", 0) or 0)
                evlu_total += float(h.get("ovrs_stck_evlu_amt", 0) or 0)
            except (ValueError, TypeError):
                continue
        if pchs_total <= 0:
            return ""
        pnl_pct = (pnl_total / pchs_total) * 100
        sign = "+" if pnl_total >= 0 else ""
        return (
            f"*💼 현재 계좌 (실거래)*\n"
            f"  총 평가액:   ${evlu_total:,.2f}\n"
            f"  매입 원금:   ${pchs_total:,.2f}\n"
            f"  평가 손익:   {sign}${pnl_total:,.2f} ({sign}{pnl_pct:.2f}%)\n"
        )
    except Exception as e:
        logger.warning(f"계좌 요약 조회 실패: {e}")
        return ""


def _get_today_trade_summary() -> str:
    """오늘 매수/매도 거래 통계 (KST 기준)."""
    try:
        from app.db.supabase import supabase
        from datetime import datetime
        import pytz
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        today_kst = now_kst.strftime("%Y-%m-%d")
        # KST 자정 = 전날 UTC 15:00
        utc_start = (now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
                     .astimezone(pytz.UTC))
        utc_start_str = utc_start.strftime("%Y-%m-%dT%H:%M:%S")

        res = supabase.table("trade_records").select(
            "id, ticker, status, buy_price, quantity, sell_price, profit_loss"
        ).gte("created_at", utc_start_str).execute()
        rows = res.data or []
        buy_count = 0
        buy_amount = 0.0
        sell_count = 0
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
            f"  매수: {buy_count}건 / ${buy_amount:,.2f}\n"
            f"  매도: {sell_count}건 / 실현손익 {sign}${realized_pnl:,.2f}\n"
        )
    except Exception as e:
        logger.warning(f"오늘 거래 요약 조회 실패: {e}")
        return ""


def _calc_holding_days(buy_date_str: Optional[str]) -> Optional[int]:
    """매수일 기준 보유 일수 계산."""
    if not buy_date_str:
        return None
    try:
        from datetime import datetime
        import pytz
        # buy_date 는 NY 시간 'YYYY-MM-DD HH:MM:SS' 또는 ISO 형식
        date_part = buy_date_str[:10]
        buy_dt = datetime.strptime(date_part, "%Y-%m-%d")
        now_ny = datetime.now(pytz.timezone('America/New_York')).replace(tzinfo=None)
        days = (now_ny.date() - buy_dt.date()).days
        return max(days, 0)
    except Exception:
        return None


def notify_buy_filled(
    ticker: str,
    stock_name: str,
    qty: int,
    fill_price: float,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    composite_score: Optional[float] = None,
):
    """매수 실제 체결 확인 후 호출 (보유 현황표 + 계좌 요약 + 오늘 거래 통계 자동 첨부)."""
    total_amount = qty * fill_price

    parts = [
        f"*이번 거래*",
        f"  {qty}주 @ ${fill_price:.2f} = *${total_amount:,.2f}*",
    ]
    if composite_score is not None:
        parts.append(f"  종합점수: {composite_score:.4f} (LLM BUY)")

    # 자동 청산 라인
    if take_profit_price and stop_loss_price and fill_price > 0:
        tp_pct = (take_profit_price - fill_price) / fill_price * 100
        sl_pct = (stop_loss_price - fill_price) / fill_price * 100
        rr = tp_pct / abs(sl_pct) if sl_pct != 0 else 0
        parts.append("")
        parts.append(f"*🎯 자동 청산 라인 (ATR 기반)*")
        parts.append(f"  익절가: ${take_profit_price:.2f} (+{tp_pct:.2f}%)  ← 도달 시 자동 매도")
        parts.append(f"  손절가: ${stop_loss_price:.2f} ({sl_pct:.2f}%)  ← 도달 시 자동 손절")
        parts.append(f"  보상/위험: {rr:.2f} : 1")

    today_summary = _get_today_trade_summary()
    if today_summary:
        parts.append("")
        parts.append(today_summary.rstrip())

    account_summary = _get_account_summary()
    if account_summary:
        parts.append("")
        parts.append(account_summary.rstrip())

    holdings_table = format_holdings_table()
    parts.append("")
    parts.append(f"*📊 현재 보유 종목*")
    parts.append(holdings_table)

    _send(
        title=f"✅ 매수 체결: {stock_name} ({ticker})",
        message="\n".join(parts),
        color="#36a64f",  # 초록 (성공)
    )


# ──────────────────────────────────────────────────────────
# ④ 매도 (주문 접수 + 체결 확인 — 2단계)
# ──────────────────────────────────────────────────────────

def notify_sell_ordered(
    ticker: str,
    stock_name: str,
    qty: int,
    price: float,
    sell_reason: str,
):
    """매도 주문 접수 직후 호출 (실 체결 X, KIS에 주문 전송 성공만 의미)."""
    _send(
        title=f"📋 매도 주문 접수: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *주문가(지정가):* ${price:.2f}  *사유:* `{sell_reason}`\n"
            f"_⏳ 거래소 체결은 정규 시간(NY 09:30~16:00) 매칭 후 별도 '체결' 알림 발송_"
        ),
        color="#3b82f6",  # 파란색 (정보성)
    )


def notify_sell_filled(
    ticker: str,
    stock_name: str,
    qty: int,
    fill_price: float,
    sell_reason: str,
    profit_loss: float,
    profit_loss_pct: float,
    buy_price: Optional[float] = None,
    buy_date: Optional[str] = None,
):
    """매도 실제 체결 확인 후 호출 (이번 거래 + 오늘 통계 + 계좌 요약 + 보유 현황표)."""
    is_profit = profit_loss >= 0
    icon = "💰" if is_profit else "🩸"
    color = "#2eb886" if is_profit else "#ff9800"
    sign = "+" if is_profit else ""

    # 매도 사유 한글 매핑
    reason_kr = {
        "take_profit": "익절 (목표가 도달)",
        "stop_loss": "손절 (손실 한도 도달)",
        "signal": "기술 신호 매도",
        "panic_sell": "패닉셀 (당일 급락+거래량 폭증)",
    }.get(sell_reason, sell_reason)

    parts = [
        f"*이번 거래*",
        f"  {qty}주 @ ${fill_price:.2f}",
    ]
    if buy_price and buy_price > 0:
        parts.append(f"  매수가 ${buy_price:.2f} → 매도가 ${fill_price:.2f}")
    parts.append(f"  손익: *{sign}${profit_loss:,.2f}* ({sign}{profit_loss_pct:.2f}%)")
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

    holdings_table = format_holdings_table()
    parts.append("")
    parts.append(f"*📊 매도 후 보유 종목*")
    parts.append(holdings_table)

    _send(
        title=f"{icon} 매도 체결: {stock_name} ({ticker})  {sign}${profit_loss:,.2f} ({sign}{profit_loss_pct:.2f}%)",
        message="\n".join(parts),
        color=color,
    )


# ──────────────────────────────────────────────────────────
# 하위 호환 alias (legacy 코드가 있을 경우 대비)
# ──────────────────────────────────────────────────────────
notify_buy_executed = notify_buy_ordered
notify_sell_executed = notify_sell_ordered


# ──────────────────────────────────────────────────────────
# ⑤ 일일 파이프라인 실패
# ──────────────────────────────────────────────────────────

def notify_pipeline_failure(
    failed_step: str,
    step_name: str,
    error: str,
    completed_steps: Optional[Dict[str, dict]] = None,
):
    """
    일일 파이프라인 (Step 1~4) 실패 알림.

    Args:
        failed_step: 실패 단계 키 (예: "2_kaggle_ml")
        step_name: 실패 단계 한글명 (예: "Kaggle ML 예측")
        error: 실패 사유 (Exception message)
        completed_steps: 이미 성공한 단계들 dict (Optional)
    """
    fields = {"❌ 실패 단계": f"{step_name}\n({failed_step})"}
    if completed_steps:
        for k, v in completed_steps.items():
            elapsed = v.get("elapsed_sec", "?")
            sn = v.get("step_name", k)
            fields[f"✅ {sn}"] = f"{elapsed}초"

    _send(
        title=f"❌ Pipeline 실패 — {step_name}",
        message=(
            f"일일 자동매매 파이프라인이 *{step_name}* 단계에서 실패했습니다.\n"
            f"이번 사이클의 매수는 진행되지 않습니다.\n\n"
            f"*에러:*\n```{(error or '')[:500]}```"
        ),
        color="#ff0000",
        fields=fields,
    )


# ──────────────────────────────────────────────────────────
# ⑥ LLM 검토 전체 실패 (Fail-Close 매수 차단)
# ──────────────────────────────────────────────────────────

def notify_llm_failure(reason: str, candidate_count: int = 0):
    """
    LLM 검토 (Claude API) 전체 실패 알림. Opus + Sonnet 폴백 포함 모두 실패 시.

    Fail-Close 정책상 매수가 차단되므로 즉시 알림 필수.

    Args:
        reason: 실패 사유 (마지막 에러 메시지 등)
        candidate_count: 검토 시도한 후보 종목 수
    """
    _send(
        title="❌ LLM 검토 실패 — 매수 차단",
        message=(
            f"Claude API 호출이 전체 실패했습니다 (Opus 3회 + Sonnet 3회).\n"
            f"Fail-Close 안전 정책으로 *오늘 매수 진행 안 함*.\n\n"
            f"검토 시도 후보: *{candidate_count}개*\n\n"
            f"*에러:*\n```{(reason or '')[:500]}```"
        ),
        color="#ff0000",
        fields={
            "조치 권장": "Anthropic API 키 / 잔액 / 서비스 상태 확인",
        },
    )

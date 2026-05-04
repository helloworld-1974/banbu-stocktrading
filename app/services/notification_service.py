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

def notify_buy_executed(
    ticker: str,
    stock_name: str,
    qty: int,
    price: float,
    composite_score: float,
):
    """매수 주문 성공 직후 호출. 보유 현황표 자동 첨부."""
    holdings_table = format_holdings_table()
    _send(
        title=f"🛒 매수 체결: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *체결가:* ${price:.2f}  "
            f"*총액:* ${qty * price:,.2f}\n"
            f"*composite_score:* {composite_score:.4f}\n\n"
            f"*📊 현재 보유 종목 + 수익률*\n{holdings_table}"
        ),
        color="#36a64f",
    )


# ──────────────────────────────────────────────────────────
# ④ 매도 체결
# ──────────────────────────────────────────────────────────

def notify_sell_executed(
    ticker: str,
    stock_name: str,
    qty: int,
    price: float,
    sell_reason: str,
    profit_loss: float,
    profit_loss_pct: float,
):
    """매도 주문 성공 직후 호출. 보유 현황표 자동 첨부."""
    is_profit = profit_loss >= 0
    icon = "💰" if is_profit else "🩸"
    color = "#2eb886" if is_profit else "#ff9800"
    sign = "+" if is_profit else ""

    holdings_table = format_holdings_table()
    _send(
        title=f"{icon} 매도 체결: {stock_name} ({ticker})",
        message=(
            f"*수량:* {qty}주  *체결가:* ${price:.2f}\n"
            f"*손익:* {sign}${profit_loss:,.2f}  "
            f"({sign}{profit_loss_pct:.2f}%)  *사유:* `{sell_reason}`\n\n"
            f"*📊 매도 후 현재 보유 종목 + 수익률*\n{holdings_table}"
        ),
        color=color,
    )


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

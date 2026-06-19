"""
매수 후보 종목 Claude API 최종 검토 (한국주식판).

LLM 은 거부권만 행사 (BUY → HOLD 변경만 가능). 호출 전체 실패 시 Fail-Close 로 매수 차단.
미국판과 동일 로직, 프롬프트만 한국 시장 맥락으로, 통화는 원(₩), 테이블은 *_kor.
"""
import json
import time
from datetime import datetime
import anthropic

from app_kor.core.config import settings
from app_kor.db.supabase import supabase
from app_kor.core.constants import TABLE_LLM_LOGS
from app_kor.services.notification_service import notify_llm_failure

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]
MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]
MODELS_WITHOUT_TEMPERATURE = {"claude-opus-4-8"}


def _save_llm_decision_logs(candidates: list, decision_map: dict, market_analysis: str, vix_value: float = None):
    """LLM 판단 결과를 llm_decision_logs_kor 에 저장 (decision_date+ticker upsert)."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        for candidate in candidates:
            ticker = candidate["ticker"]
            decision_data = decision_map.get(ticker, {})
            supabase.table(TABLE_LLM_LOGS).upsert({
                "decision_date": today,
                "ticker": ticker,
                "stock_name": candidate.get("stock_name"),
                "decision": decision_data.get("decision", "N/A"),
                "reason": decision_data.get("reason", ""),
                "market_analysis": market_analysis,
                "composite_score": candidate.get("composite_score"),
                "rise_probability": candidate.get("rise_probability"),
                "rsi": candidate.get("rsi"),
                "adx": candidate.get("adx"),
                "vix_value": vix_value,
                "updated_at": datetime.now().isoformat(),
            }, on_conflict="decision_date,ticker").execute()
        print(f"  LLM 판단 로그 저장 완료: {len(candidates)}건")
    except Exception as log_e:
        print(f"  LLM 판단 로그 저장 실패: {log_e}")


def review_buy_candidates(candidates: list, vix_value: float = None) -> dict:
    """매수 후보를 Claude API 로 최종 검토. LLM 은 거부권만 (BUY→HOLD)."""
    if not settings.ANTHROPIC_API_KEY:
        msg = "ANTHROPIC_API_KEY가 설정되지 않았습니다. LLM 검토 불가로 매수 차단."
        print(f"  {msg}")
        try:
            notify_llm_failure(reason=msg, candidate_count=len(candidates))
        except Exception as notify_e:
            print(f"  LLM 실패 알림 발송 실패: {notify_e}")
        return {"reviewed_candidates": [], "held_candidates": candidates, "llm_reasoning": "API 키 미설정으로 매수 차단", "raw_response": []}

    if not candidates:
        return {"reviewed_candidates": [], "held_candidates": [], "llm_reasoning": "매수 후보 없음", "raw_response": []}

    stock_summaries = []
    for i, c in enumerate(candidates, 1):
        summary = f"""
{i}. {c.get('stock_name', 'N/A')} ({c.get('ticker', 'N/A')})
   - ML 예측: 예측 상승률 +{c.get('rise_probability', 0):.2f}% (현재가 {c.get('last_price', 0):,.0f}원 → 예측가 {c.get('predicted_price', 0):,.0f}원)
   - 기술적 지표:
     골든크로스: {'✓' if c.get('golden_cross') else '✗'} (SMA20: {c.get('sma20', 0):,.0f}, SMA50: {c.get('sma50', 0):,.0f})
     RSI: {c.get('rsi', 0):.2f} {'(과매도 반등)' if c.get('rsi', 50) < 30 else '(강세 진입)' if 50 <= c.get('rsi', 50) <= 65 else '(매수구간 아님)'}
     MACD: {c.get('macd', 0):.4f}, Signal: {c.get('signal', 0):.4f}, 매수신호: {'✓' if c.get('macd_buy_signal') else '✗'}
   - 거래량: 5일 평균 대비 {c.get('volume_ratio', 'N/A')}배
   - ADX(추세강도): {c.get('adx', 'N/A')} {'(강한 추세)' if c.get('adx') and c.get('adx') > 25 else '(추세 약함)' if c.get('adx') and c.get('adx') < 20 else '(보통)'}
   - 감성분석: {c.get('sentiment_score', 'N/A')} (기사 {c.get('article_count', 0)}개)
   - 종합점수: {c.get('composite_score', 0):.4f}"""
        stock_summaries.append(summary)

    today = datetime.now().strftime("%Y-%m-%d")
    stocks_text = "\n".join(stock_summaries)

    prompt = f"""당신은 한국 주식 시장(KOSPI/KOSDAQ) 경력 20년의 트레이딩 전문가이자 최종 의사결정자입니다.

## 당신의 역할
아래 종목들은 자동매매 시스템(팀원)이 ML 예측, 기술적 분석, 감성분석, 변동성지수를 종합하여 매수 후보로 올린 한국 종목입니다.
당신은 팀장으로서 팀원의 분석을 최종 검토하고 BUY 또는 HOLD를 판정합니다.
제공된 데이터와 당신의 한국 시장 지식을 종합하여 독립적으로 판단하세요.

## 오늘 날짜
{today}

## 시장 환경
- 변동성지수(VIX, 글로벌 공포지수 대용): {vix_value if vix_value else 'N/A'}

## 매수 후보 종목
{stocks_text}

## 검토 기준
### 기술적 지표 검증
- 골든크로스가 발생했지만 현재가가 이동평균선보다 크게 하회하면 유효한 신호인지 의심
- RSI 과매도(< 30)는 반등 기회일 수 있지만, ADX가 약하면(< 20) 추세 없는 횡보일 수 있음
- RSI 과매수(> 70)인 종목이 후보에 포함되었다면 시스템 오류 가능성 → HOLD

### 한국 시장 특화 리스크
- 해당 종목의 실적 발표가 1주일 이내 예정 → HOLD
- 금통위(한국은행 기준금리), 미국 FOMC/CPI 등 매크로 이벤트가 1~2일 내 → HOLD 고려
- 공매도 과열종목 지정, 투자경고/위험 지정, 거래정지 이력 등 → HOLD
- 특이 리스크(오너 리스크, 소송, 규제, 유상증자 등) → HOLD

### 포트폴리오 균형
- 같은 섹터(예: 2차전지, 반도체) 3개 이상 집중 시 → 가장 약한 종목을 HOLD (전부 HOLD하지 말 것)

## 판정 원칙
- BUY와 HOLD 모두 구체적 근거 제시.
- 막연한 불안이 아닌 명확한 데이터/사실 기반 판단.
- 균형 잡힌 판단 (살 만한 종목은 매수, 위험한 종목은 거부).

## 응답 형식
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "market_analysis": "시장 전체에 대한 간단한 분석 (1~2문장)",
  "decisions": [
    {{
      "ticker": "종목 코드",
      "stock_name": "종목명",
      "decision": "BUY 또는 HOLD",
      "reason": "판정 이유 (1~2문장)"
    }}
  ]
}}"""

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    last_error = None

    for model in MODELS:
        for attempt in range(MAX_RETRIES):
            try:
                print(f"  LLM 호출 시도 {attempt + 1}/{MAX_RETRIES} (모델: {model})")
                create_kwargs = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]}
                if model not in MODELS_WITHOUT_TEMPERATURE:
                    create_kwargs["temperature"] = 0
                message = client.messages.create(**create_kwargs)

                response_text = message.content[0].text.strip()
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                response_data = json.loads(response_text)

                decisions = response_data.get("decisions", [])
                market_analysis = response_data.get("market_analysis", "")
                used_model_note = f" (폴백: {model})" if model != MODELS[0] else ""
                decision_map = {d["ticker"]: d for d in decisions}

                reviewed, held = [], []
                for candidate in candidates:
                    ticker = candidate["ticker"]
                    decision = decision_map.get(ticker, {})
                    llm_decision = decision.get("decision", "HOLD").upper()
                    llm_reason = decision.get("reason", "LLM 응답 없음")
                    candidate["llm_decision"] = llm_decision
                    candidate["llm_reason"] = llm_reason
                    if llm_decision == "BUY":
                        reviewed.append(candidate)
                    else:
                        held.append(candidate)
                        print(f"  LLM HOLD: {candidate['stock_name']}({ticker}) - {llm_reason}")

                print(f"  LLM 검토 완료{used_model_note}: {len(reviewed)} BUY / {len(held)} HOLD")
                _save_llm_decision_logs(candidates, decision_map, market_analysis, vix_value)
                return {"reviewed_candidates": reviewed, "held_candidates": held,
                        "llm_reasoning": market_analysis + used_model_note, "raw_response": decisions}

            except json.JSONDecodeError as e:
                print(f"  LLM 응답 JSON 파싱 실패 (시도 {attempt + 1}): {e}")
                last_error = e
                break
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                last_error = e
                error_code = getattr(e, 'status_code', 0)
                delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
                if error_code in (429, 503, 529):
                    print(f"  LLM 서버 과부하/속도제한 (시도 {attempt + 1}, 모델 {model}): {e} → {delay}초 후 재시도")
                    time.sleep(delay)
                    continue
                else:
                    print(f"  LLM API 에러 (시도 {attempt + 1}, 모델 {model}): {e}")
                    break
            except Exception as e:
                last_error = e
                delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
                print(f"  LLM 호출 실패 (시도 {attempt + 1}, 모델 {model}): {e} → {delay}초 후 재시도")
                time.sleep(delay)
                continue

        if model != MODELS[-1]:
            print(f"  {model} 전체 실패. 폴백 모델 {MODELS[MODELS.index(model) + 1]}로 전환합니다.")

    fail_reason = f"LLM 호출 전체 실패 (Opus {MAX_RETRIES}회 + Sonnet {MAX_RETRIES}회): {str(last_error)}"
    print(f"  {fail_reason}")
    fail_decision_map = {c["ticker"]: {"decision": "FAIL", "reason": fail_reason} for c in candidates}
    _save_llm_decision_logs(candidates, fail_decision_map, fail_reason, vix_value)
    try:
        notify_llm_failure(reason=fail_reason, candidate_count=len(candidates))
    except Exception as notify_e:
        print(f"  LLM 실패 알림 발송 실패: {notify_e}")

    return {"reviewed_candidates": [], "held_candidates": candidates, "llm_reasoning": fail_reason, "raw_response": []}

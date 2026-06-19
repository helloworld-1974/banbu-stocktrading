"""
한국주식 일일 매수 파이프라인 통합 API.

  - POST /pipeline/run-buy-pipeline   : 기술지표 + 감성 + 통합후보 + LLM 검토 (매수 주문 X)
  - Kaggle: /pipeline/kaggle/auth-check, /status, /trigger-ml
  - POST /pipeline/run-full-daily     : 4단계 전체 (경제데이터→Kaggle ML→기술+감성→LLM+매수)
"""
import asyncio
import time
import logging
from fastapi import APIRouter, HTTPException

from app_kor.services.stock_recommendation_service import StockRecommendationService
from app_kor.services.llm_review_service import review_buy_candidates
from app_kor.services import ml_trigger_service
from app_kor.utils.scheduler import _execute_daily_pipeline

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/run-buy-pipeline", response_model=dict, summary="기술지표 + 감성분석 + LLM 검토 통합 실행")
async def run_buy_pipeline():
    """매수 후보 산출 + LLM 최종 검토를 순차 실행 (실제 매수 주문은 발생하지 않음)."""
    pipeline_start = time.time()
    steps_summary = {}
    try:
        service = StockRecommendationService()

        logger.info("[1/4] 기술 지표 생성 시작")
        step_start = time.time()
        tech_results = service.generate_technical_recommendations()
        steps_summary["1_technical_analysis"] = {
            "message": tech_results["message"], "count": len(tech_results.get("data", [])),
            "elapsed_sec": round(time.time() - step_start, 1),
        }

        logger.info("[2/4] 뉴스 감성 분석 시작")
        step_start = time.time()
        sentiment_results = service.fetch_and_store_sentiment_for_recommendations()
        steps_summary["2_sentiment_analysis"] = {
            "message": sentiment_results["message"], "count": len(sentiment_results.get("results", [])),
            "elapsed_sec": round(time.time() - step_start, 1),
        }

        logger.info("[3/4] 매수 후보 추출 시작")
        step_start = time.time()
        recommendations = service.get_combined_recommendations_with_technical_and_sentiment()
        candidates = recommendations.get("results", [])
        steps_summary["3_candidate_extraction"] = {
            "message": recommendations.get("message", ""), "count": len(candidates),
            "elapsed_sec": round(time.time() - step_start, 1),
        }

        if not candidates:
            total_elapsed = time.time() - pipeline_start
            return {
                "message": "매수 후보가 없어 LLM 검토를 건너뜁니다", "steps": steps_summary,
                "candidates_before_llm": 0, "candidates_after_llm": 0, "held": 0,
                "results": [], "held_results": [], "llm_reasoning": "",
                "total_elapsed_sec": round(total_elapsed, 1),
            }

        logger.info("[4/4] LLM 검토 시작")
        step_start = time.time()
        vix_value = candidates[0].get("vix_value") if candidates else None
        review_result = review_buy_candidates(candidates, vix_value)
        steps_summary["4_llm_review"] = {
            "buy_count": len(review_result["reviewed_candidates"]),
            "hold_count": len(review_result["held_candidates"]),
            "elapsed_sec": round(time.time() - step_start, 1),
        }

        total_elapsed = time.time() - pipeline_start
        return {
            "message": f"통합 파이프라인 완료 - BUY {len(review_result['reviewed_candidates'])}개 / HOLD {len(review_result['held_candidates'])}개",
            "steps": steps_summary,
            "candidates_before_llm": len(candidates),
            "candidates_after_llm": len(review_result["reviewed_candidates"]),
            "held": len(review_result["held_candidates"]),
            "results": review_result["reviewed_candidates"],
            "held_results": review_result["held_candidates"],
            "llm_reasoning": review_result["llm_reasoning"],
            "total_elapsed_sec": round(total_elapsed, 1),
        }
    except Exception as e:
        logger.error(f"통합 파이프라인 중 오류 발생: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"통합 파이프라인 중 오류 발생: {str(e)}")


# ── Kaggle 연동 ────────────────────────────────────────────────

@router.get("/kaggle/auth-check", response_model=dict, summary="Kaggle 인증 확인")
async def kaggle_auth_check():
    try:
        ok, msg = await asyncio.to_thread(ml_trigger_service.check_auth)
        return {"ok": ok, "message": msg}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kaggle/status", response_model=dict, summary="Kaggle 노트북 실행 상태 조회")
async def kaggle_status():
    try:
        status = await asyncio.to_thread(ml_trigger_service.get_status)
        return {
            "kernel": f"{ml_trigger_service.settings.KAGGLE_USERNAME}/{ml_trigger_service.settings.KAGGLE_KERNEL_SLUG}",
            "status": status,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/kaggle/trigger-ml", response_model=dict, summary="Kaggle ML 노트북 트리거 + 완료 대기 (최대 15분)")
async def kaggle_trigger_ml(max_wait_sec: int = 900):
    try:
        success, message, meta = await asyncio.to_thread(
            ml_trigger_service.trigger_and_wait, ml_trigger_service.POLL_INTERVAL_SEC, max_wait_sec,
        )
        return {"success": success, "message": message, "meta": meta}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 4단계 일일 파이프라인 ──────────────────────────────────────

@router.post("/run-full-daily", response_model=dict, summary="4단계 일일 파이프라인 통합 실행")
async def run_full_daily():
    """경제데이터 → Kaggle ML → 기술+감성 → LLM+매수. 단계 실패 시 500 즉시 중단."""
    result = await _execute_daily_pipeline()
    if not result["success"]:
        raise HTTPException(
            status_code=500,
            detail={
                "failed_step": result["failed_step"], "step_name": result["step_name"],
                "error": result["error"], "elapsed_sec": result["total_elapsed_sec"],
                "completed_steps": result["completed_steps"],
            },
        )
    return {
        "success": True, "message": "전체 파이프라인 4단계 모두 성공",
        "total_elapsed_sec": result["total_elapsed_sec"], "steps": result["completed_steps"],
    }

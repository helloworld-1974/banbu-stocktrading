from fastapi import APIRouter, HTTPException
from app_kor.services.stock_recommendation_service import StockRecommendationService
from app_kor.utils.scheduler import (
    run_auto_buy_now, start_scheduler, stop_scheduler, stock_scheduler,
    run_auto_sell_now, start_sell_scheduler, stop_sell_scheduler,
)

router = APIRouter()
service = StockRecommendationService()


@router.get("/recommended-stocks", response_model=dict)
async def get_recommended_stocks_route():
    """ML 예측 상승확률 >= 2% 종목 목록 반환 (상승확률 내림차순)."""
    try:
        return service.get_stock_recommendations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"추천 주식 조회 중 오류 발생: {str(e)}")


@router.get("/recommended-stocks/with-sentiment", response_model=dict)
async def get_recommended_stocks_with_sentiment():
    """ML 추천 + 감성(>= 0.15) 결합."""
    try:
        result = service.get_recommendations_with_sentiment()
        if not result["results"]:
            return {"message": "조건을 만족하는 추천 주식이 없습니다", "results": []}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"추천 주식 및 감정 분석 조회 중 오류 발생: {str(e)}")


@router.post("/recommended-stocks/analyze-news-sentiment", response_model=dict)
async def analyze_news_sentiment():
    """추천+보유 종목 뉴스 감정 분석 수행 후 저장."""
    try:
        return service.fetch_and_store_sentiment_for_recommendations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"뉴스 감정 분석 중 오류 발생: {str(e)}")


@router.post("/recommended-stocks/generate-technical-recommendations", response_model=dict)
async def generate_technical_recommendations():
    """기술적 지표 기반 추천 데이터 생성 후 저장."""
    try:
        recommendations = service.generate_technical_recommendations()
        return {"message": "기술적 추천 데이터가 성공적으로 생성되고 저장되었습니다", "data": recommendations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"기술적 추천 데이터 생성 중 오류 발생: {str(e)}")


@router.get("/recommended-stocks/with-technical-and-sentiment", response_model=dict)
async def get_recommended_stocks_with_technical_and_sentiment():
    """ML + 기술 + 감성 통합 매수 추천."""
    try:
        return service.get_combined_recommendations_with_technical_and_sentiment()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"기술적 지표 및 감정 분석 조회 중 오류 발생: {str(e)}")


@router.post("/recommended-stocks/generate-complete-analysis", response_model=dict)
async def generate_complete_analysis():
    """기술적 지표 생성 + 뉴스 감정 분석 + 통합 조회를 하나로."""
    try:
        tech_results = service.generate_technical_recommendations()
        sentiment_results = service.fetch_and_store_sentiment_for_recommendations()
        combined_results = service.get_combined_recommendations_with_technical_and_sentiment()
        return {
            "message": "통합 분석이 완료되었습니다",
            "technical_analysis": {"message": tech_results["message"], "count": len(tech_results.get("data", []))},
            "sentiment_analysis": {"message": sentiment_results["message"], "count": len(sentiment_results.get("results", []))},
            "combined_results": combined_results,
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"통합 분석 중 오류 발생: {str(e)}")


@router.get("/sell-candidates", response_model=dict)
async def get_sell_candidates():
    """매도 대상 종목 조회 (ATR 익절/손절 + 기술 신호 + 감성 + VIX)."""
    try:
        return service.get_stocks_to_sell()
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"매도 대상 종목 조회 중 오류 발생: {str(e)}")


@router.post("/purchase/trigger", response_model=dict)
async def trigger_auto_purchase():
    """자동 매수 프로세스 수동 트리거 (force 실행)."""
    try:
        run_auto_buy_now()
        return {"message": "자동 매수 프로세스가 트리거되었습니다. 로그를 확인하세요."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"자동 매수 트리거 중 오류 발생: {str(e)}")


@router.post("/purchase/scheduler/start", response_model=dict)
async def start_auto_purchase_scheduler():
    """자동 매수 스케줄러 시작 (평일 09:05~09:10 KST)."""
    try:
        result = start_scheduler()
        return {"message": "자동 매수 스케줄러가 시작되었습니다." if result else "자동 매수 스케줄러가 이미 실행 중입니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스케줄러 시작 중 오류 발생: {str(e)}")


@router.post("/purchase/scheduler/stop", response_model=dict)
async def stop_auto_purchase_scheduler():
    """자동 매수 스케줄러 중지."""
    try:
        result = stop_scheduler()
        return {"message": "자동 매수 스케줄러가 중지되었습니다." if result else "자동 매수 스케줄러가 이미 중지되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스케줄러 중지 중 오류 발생: {str(e)}")


@router.get("/scheduler/status", response_model=dict)
async def get_scheduler_status_route():
    """자동 매수/매도 스케줄러 상태 반환."""
    try:
        buy_running = stock_scheduler.running
        sell_running = stock_scheduler.sell_running
        return {
            "buy_running": buy_running,
            "sell_running": sell_running,
            "message": f"매수 스케줄러: {'실행 중' if buy_running else '중지됨'}, 매도 스케줄러: {'실행 중' if sell_running else '중지됨'}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스케줄러 상태 확인 중 오류 발생: {str(e)}")


@router.post("/sell/trigger", response_model=dict)
async def trigger_auto_sell():
    """자동 매도 프로세스 수동 트리거."""
    try:
        run_auto_sell_now()
        return {"message": "자동 매도 프로세스가 트리거되었습니다. 로그를 확인하세요."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"자동 매도 트리거 중 오류 발생: {str(e)}")


@router.post("/sell/scheduler/start", response_model=dict)
async def start_auto_sell_scheduler():
    """자동 매도 스케줄러 시작 (1분마다 매도 대상 확인)."""
    try:
        result = start_sell_scheduler()
        return {"message": "자동 매도 스케줄러가 시작되었습니다." if result else "자동 매도 스케줄러가 이미 실행 중입니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매도 스케줄러 시작 중 오류 발생: {str(e)}")


@router.post("/sell/scheduler/stop", response_model=dict)
async def stop_auto_sell_scheduler():
    """자동 매도 스케줄러 중지."""
    try:
        result = stop_sell_scheduler()
        return {"message": "자동 매도 스케줄러가 중지되었습니다." if result else "자동 매도 스케줄러가 이미 중지되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매도 스케줄러 중지 중 오류 발생: {str(e)}")

from fastapi import APIRouter, HTTPException, Query
from app.services.predict_service import (
    run_prediction_async,
    get_status,
    get_predictions,
    get_analysis,
)

router = APIRouter()


@router.post("/run", summary="주가 예측 실행")
def run_predict(
    skip_train: bool = Query(False, description="True: 저장된 모델 사용, False: 새로 학습"),
):
    """
    Transformer 모델로 주가 예측을 실행합니다 (백그라운드).

    - **skip_train=false**: 전체 학습 + 예측 (수십 분 소요)
    - **skip_train=true**: 저장된 모델로 예측만 수행 (수 분 소요)

    실행 상태는 GET /predict/status 로 확인하세요.
    """
    result = run_prediction_async(skip_train=skip_train)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.get("/status", summary="예측 실행 상태 확인")
def predict_status():
    """현재 예측 작업의 진행 상태를 반환합니다."""
    status = get_status()
    result = {
        "state": status["state"],
        "message": status["message"],
        "progress": status["progress"],
    }
    if status["started_at"]:
        import time
        elapsed = (status["completed_at"] or time.time()) - status["started_at"]
        result["elapsed_seconds"] = round(elapsed, 1)
    if status["error"]:
        result["error"] = status["error"]
    return result


@router.get("/results", summary="예측 결과 조회")
def predict_results():
    """predicted_stocks 테이블의 최근 예측 결과를 반환합니다."""
    try:
        data = get_predictions()
        return {"count": len(data), "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis", summary="분석 결과 조회")
def predict_analysis():
    """stock_analysis_results 테이블의 분석 결과를 반환합니다."""
    try:
        data = get_analysis()
        return {"count": len(data), "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

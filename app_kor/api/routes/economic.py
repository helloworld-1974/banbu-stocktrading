from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from app_kor.schemas.stock import UpdateResponse
from app_kor.utils.scheduler import run_economic_data_update_now

router = APIRouter()


@router.post("/update", summary="한국 경제 및 주식 데이터 업데이트", response_model=UpdateResponse)
async def update_economic_data(
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="True이면 장 중에도 강제 수집"),
):
    """
    한국 경제/주가 데이터를 economic_and_stock_data_kor 에 저장합니다.
    백그라운드에서 실행되어 API 응답을 블로킹하지 않습니다.
    DB 마지막 수집 날짜를 자동으로 찾아 그 다음 날부터 수집합니다.
    """
    try:
        background_tasks.add_task(run_economic_data_update_now, force=force)
        return {
            "success": True,
            "message": f"한국 경제 데이터 업데이트가 백그라운드에서 시작되었습니다.{' (강제 수집 모드)' if force else ''}",
            "total_records": 0,
            "updated_records": 0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"데이터 업데이트 중 오류 발생: {str(e)}")

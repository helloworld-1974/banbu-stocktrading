from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app_kor.api.api import api_router
from app_kor.services.economic_service import update_economic_data_in_background
from app_kor.utils.scheduler import (
    start_scheduler, stop_scheduler,
    start_sell_scheduler, stop_sell_scheduler,
    start_economic_data_scheduler, stop_economic_data_scheduler,
    start_daily_pipeline_scheduler, stop_daily_pipeline_scheduler,
)
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once when app starts
    await startup()
    yield
    # Shutdown: 정리 작업
    stop_scheduler()                    # 매수 스케줄러 종료
    stop_sell_scheduler()               # 매도 스케줄러 종료
    stop_economic_data_scheduler()      # 경제 데이터 스케줄러 종료
    stop_daily_pipeline_scheduler()     # 일일 통합 파이프라인 스케줄러 종료


app = FastAPI(title="한국주식 분석 및 추천 API", lifespan=lifespan)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록
app.include_router(api_router)


@app.get("/")
def read_root():
    return {"message": "한국주식 분석 및 추천 API에 오신 것을 환영합니다"}


async def startup():
    # 서비스 시작 시 경제 데이터 수집 즉시 실행
    print("서비스 시작 시 한국 경제/주가 데이터 수집을 즉시 실행합니다...")
    await update_economic_data_in_background()
    print("초기 경제 데이터 수집이 완료되었습니다.")

    # 주식 자동매매 스케줄러 시작
    start_scheduler()
    start_sell_scheduler()

    # 일일 통합 파이프라인 스케줄러 시작 (매일 KST 18:00)
    start_daily_pipeline_scheduler()


if __name__ == "__main__":
    uvicorn.run("app_kor.main:app", host="0.0.0.0", port=8001, reload=False)

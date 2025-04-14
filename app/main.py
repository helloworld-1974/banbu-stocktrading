from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.api.api import api_router
from app.services.economic_service import update_economic_data_in_background
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once when app starts
    await startup()
    yield
    # Shutdown: 필요한 정리 작업

app = FastAPI(title="주식 분석 및 추천 API", lifespan=lifespan)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 오리진 허용 (프로덕션에서는 특정 도메인으로 제한 권장)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록 (중앙 관리 방식)
app.include_router(api_router)

@app.get("/")
def read_root():
    return {"message": "주식 분석 및 추천 API에 오신 것을 환영합니다"}

# APScheduler 대신 직접 실행
async def startup():
    await update_economic_data_in_background()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
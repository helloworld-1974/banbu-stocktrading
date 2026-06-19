from pydantic import Field
from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()


class Settings(BaseSettings):
    """한국 주식 자동매매(app_kor) 설정.

    기존 app(해외주식)과 동일한 .env 를 읽되, 한국 시장 전용 값을 추가한다.
    KIS 계좌/앱키는 국내·해외 공용이므로 그대로 재사용한다.
    """
    PROJECT_NAME: str = "한국주식 분석 API"
    PROJECT_DESCRIPTION: str = "국내주식 잔고 조회 및 자동매매 API"
    PROJECT_VERSION: str = "1.0.0"

    DEBUG: bool = Field(default=False, description="디버그 모드 활성화 여부")

    CORS_ORIGINS: List[str] = ["*"]

    SUPABASE_URL: str = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")

    # ── 한국투자증권 API 설정 (국내·해외 공용) ───────────────────────
    KIS_USE_MOCK: bool = Field(default=True, description="모의투자 사용 여부")

    KIS_BASE_URL: str = Field(
        default="https://openapivts.koreainvestment.com:29443",
        description="한국투자증권 API 기본 URL (모의투자용)"
    )
    KIS_REAL_URL: str = Field(
        default="https://openapi.koreainvestment.com:9443",
        description="한국투자증권 API 기본 URL (실제투자용)"
    )

    # 모의투자 계좌 정보
    KIS_MOCK_APPKEY: str = Field(default="", description="모의투자 앱키")
    KIS_MOCK_APPSECRET: str = Field(default="", description="모의투자 앱시크릿")
    KIS_MOCK_CANO: str = Field(default="50173046", description="모의투자 계좌번호")

    # 실제투자 계좌 정보
    KIS_REAL_APPKEY: str = Field(default="", description="실제투자 앱키")
    KIS_REAL_APPSECRET: str = Field(default="", description="실제투자 앱시크릿")
    KIS_REAL_CANO: str = Field(default="64856431", description="실제투자 계좌번호")

    # .env 호환용 (직접 사용하지 않고 property로 대체)
    KIS_APPKEY: str = Field(default="", description="한국투자증권 API 앱키")
    KIS_APPSECRET: str = Field(default="", description="한국투자증권 API 앱시크릿")
    KIS_CANO: str = Field(default="", description="계좌번호 앞 8자리")
    KIS_ACNT_PRDT_CD: str = Field(default="01", description="계좌번호 뒤 2자리")

    ALPHA_VANTAGE_API_KEY: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    TR_ID: str = os.getenv("TR_ID", "")
    FRED_API_KEY: str = os.getenv("FRED_API_KEY", "aedfbcd8ba091c740281c0bd8ca93b46")

    # Kaggle API (ML 예측 노트북 트리거용) — 한국주식 전용 커널
    KAGGLE_USERNAME: str = os.getenv("KAGGLE_USERNAME", "")
    KAGGLE_API_TOKEN: str = os.getenv("KAGGLE_API_TOKEN", "")
    KAGGLE_KEY: str = os.getenv("KAGGLE_KEY", "")
    KAGGLE_KERNEL_SLUG: str = os.getenv("KAGGLE_KERNEL_SLUG_KOR", "stock-prediction-kor")
    KAGGLE_NOTEBOOK_DIR: str = os.getenv("KAGGLE_NOTEBOOK_DIR_KOR", "kaggle_notebook_kor")

    # Slack 알림 (한국주식 전용 채널 webhook 가능, 없으면 공용 webhook 사용)
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL_KOR", os.getenv("SLACK_WEBHOOK_URL", ""))
    SLACK_NOTIFY_LEVEL: str = os.getenv("SLACK_NOTIFY_LEVEL", "info")

    # Cross-sectional z-score 점수 시스템 v2 활성화
    USE_SCORING_V2: bool = os.getenv("USE_SCORING_V2", "false").lower() == "true"

    # ── 한국 시장 전용 설정 ─────────────────────────────────────────
    # OHLCV/시세 데이터 소스 우선순위 (kis → pykrx → yahoo 순 폴백)
    KOR_DATA_SOURCE_PRIORITY: str = os.getenv(
        "KOR_DATA_SOURCE_PRIORITY", "kis,pykrx,yahoo"
    )
    # 종목당 투자 비중 (총자산 대비)
    KOR_INVEST_RATIO: float = float(os.getenv("KOR_INVEST_RATIO", "0.10"))

    @property
    def kis_base_url(self) -> str:
        """사용할 한국투자증권 API URL 반환"""
        return self.KIS_BASE_URL if self.KIS_USE_MOCK else self.KIS_REAL_URL

    @property
    def data_source_priority(self) -> List[str]:
        """OHLCV 데이터 소스 우선순위 리스트"""
        return [s.strip().lower() for s in self.KOR_DATA_SOURCE_PRIORITY.split(",") if s.strip()]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # KIS_USE_MOCK에 따라 활성 계좌 정보 자동 전환
        if self.KIS_USE_MOCK:
            if self.KIS_MOCK_APPKEY:
                self.KIS_APPKEY = self.KIS_MOCK_APPKEY
            if self.KIS_MOCK_APPSECRET:
                self.KIS_APPSECRET = self.KIS_MOCK_APPSECRET
            if self.KIS_MOCK_CANO:
                self.KIS_CANO = self.KIS_MOCK_CANO
        else:
            if self.KIS_REAL_APPKEY:
                self.KIS_APPKEY = self.KIS_REAL_APPKEY
            if self.KIS_REAL_APPSECRET:
                self.KIS_APPSECRET = self.KIS_REAL_APPSECRET
            if self.KIS_REAL_CANO:
                self.KIS_CANO = self.KIS_REAL_CANO

        # 계좌번호에 상품코드가 "12345678-01" 형태로 붙어온 경우 분리.
        # CANO 는 앞 8자리, ACNT_PRDT_CD 는 뒤 2자리.
        if self.KIS_CANO and "-" in self.KIS_CANO:
            cano, _, prdt = self.KIS_CANO.partition("-")
            self.KIS_CANO = cano
            if prdt:
                self.KIS_ACNT_PRDT_CD = prdt

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# 싱글톤 설정 객체 생성
settings = Settings()

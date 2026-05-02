# Kaggle API 연동 — 우리 시스템 ML 자동화 상세 가이드

> 매일 수동으로 Colab에서 돌리던 `predict_colab.py` 를 Kaggle 무료 GPU + 우리 FastAPI 스케줄러에서 **버튼 한 번 (또는 cron)** 으로 자동 실행하기 위한 통합 가이드.

---

## 1. 한 문장 요약

> **"Kaggle API로 노트북을 push하면 자동으로 실행이 시작된다. status를 폴링해서 complete가 되면 다음 단계(기술지표 + LLM 매수)로 체이닝한다."**

이렇게 하면 Phase 2 자동화(`07_자동화_방안.md`)의 "Kaggle Schedule UI" 방식보다 **순차 실행 보장 + 실패 시 재시도** 가 가능해서 더 안정적입니다.

---

## 2. Kaggle API 가 뭔가요?

Kaggle이 공식 제공하는 CLI + Python SDK. 무료 GPU 노트북을 **외부에서 트리거 / 상태 조회 / 결과 다운로드** 할 수 있습니다.

### 2-1. 핵심 명령 (4가지면 충분)

| 명령 | 용도 | 비고 |
|---|---|---|
| `kaggle kernels push -p <폴더>` | 노트북 업로드 + **자동 실행 트리거** | 매번 호출 시 새 버전 생성 |
| `kaggle kernels status <user>/<slug>` | 실행 상태 조회 | `queued`/`running`/`complete`/`error` 등 |
| `kaggle kernels output <user>/<slug>` | 결과물 다운로드 | 우리는 DB로 출력 → 거의 안 씀 |
| `kaggle kernels pull <user>/<slug>` | 노트북 코드 다운로드 | 백업용 |

### 2-2. SDK 직접 호출 vs CLI 호출

- **CLI (`subprocess.run`)**: 설정 간단, FastAPI에서 그대로 호출 가능
- **SDK (`from kaggle.api.kaggle_api_extended import KaggleApi`)**: 더 깔끔하지만 import 시점에 인증 파일 필요

이 가이드는 **CLI 방식** 으로 갑니다 (트러블슈팅 쉬움).

---

## 3. 사전 준비

### 3-1. Kaggle 계정 + API 토큰 발급

1. [https://www.kaggle.com](https://www.kaggle.com) 가입 (이메일 인증 필수)
2. 우측 상단 프로필 → **Settings**
3. **API** 섹션 → **Create New Token** 클릭
4. `kaggle.json` 파일 자동 다운로드 → 내용 예시:
   ```json
   {"username":"your_username","key":"abcd1234efgh5678..."}
   ```

### 3-2. FastAPI 서버에 키 배치

#### 방법 1: 파일 (개발 환경에 권장)

```bash
# Linux / macOS
mkdir -p ~/.kaggle
mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json
chmod 600 ~/.kaggle/kaggle.json
```

```powershell
# Windows
mkdir $env:USERPROFILE\.kaggle
move $env:USERPROFILE\Downloads\kaggle.json $env:USERPROFILE\.kaggle\kaggle.json
```

#### 방법 2: 환경변수 (배포/Docker 환경에 권장)

`.env` 에 추가:
```bash
KAGGLE_USERNAME=your_username
KAGGLE_KEY=abcd1234efgh5678...
```

`subprocess` 호출 시 `env=` 로 전달하거나 OS 환경변수에 export.

### 3-3. Python 패키지 설치

```bash
pip install kaggle
```

`requirements.txt` 에도 추가:
```
kaggle>=1.6.0
```

### 3-4. 인증 확인

```bash
kaggle kernels list -m | head
```

자기 노트북 목록이 나오면 정상.

---

## 4. Kaggle 노트북 만들기 (한 번만)

### 4-1. 폴더 구조

프로젝트 안에 `kaggle_notebook/` 폴더 생성:

```
banbu-stocktrading-final/
└── kaggle_notebook/
    ├── kernel-metadata.json    ← 노트북 설정
    └── predict.py              ← predict_colab.py 정리본
```

### 4-2. `kernel-metadata.json`

```json
{
  "id": "your_username/stock-prediction",
  "title": "Stock Prediction Daily",
  "code_file": "predict.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": "true",
  "enable_gpu": "true",
  "enable_internet": "true",
  "dataset_sources": [],
  "competition_sources": [],
  "kernel_sources": []
}
```

#### 필드 설명

| 필드 | 설명 | 우리 값 |
|---|---|---|
| `id` | `username/slug` 형태 (kebab-case) | `your_username/stock-prediction` |
| `kernel_type` | `script` (.py) 또는 `notebook` (.ipynb) | **`script`** (.py가 git 관리 쉬움) |
| `is_private` | `"true"` 권장 | `"true"` |
| `enable_gpu` | GPU 사용 | `"true"` (필수, T4 자동 할당) |
| `enable_internet` | 외부 통신 | `"true"` (Supabase 호출 필요) |

> ⚠️ `id` 의 username은 **반드시 본인 Kaggle 사용자명** 으로 교체.

### 4-3. `predict.py` (predict_colab.py 정리본)

기존 `predict_colab.py` 에서 다음 부분만 수정:

```python
# ❌ 기존 (predict_colab.py:7)
!pip install supabase tensorflow

# ✅ Kaggle용 (Kaggle은 자동 설치, 라인 자체 삭제)
# 또는 import 위에 명시적으로:
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "supabase"])
# (Kaggle 이미지에 tensorflow는 이미 설치됨)
```

```python
# ❌ 기존 (predict_colab.py:48-49)
SUPABASE_URL=https://hcrymjkdgvvsttjecype.supabase.co
SUPABASE_KEY=eyJ...

# ✅ 환경변수로 (Kaggle Secrets로 주입)
import os
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 환경변수가 설정되지 않음")
```

> Kaggle Secrets 설정: 노트북 페이지 → **Add-ons → Secrets → Add a new secret** → `SUPABASE_URL`, `SUPABASE_KEY` 둘 다 등록.

### 4-4. 첫 푸시 (수동, 한 번만)

```bash
cd kaggle_notebook
kaggle kernels push -p .
```

성공 시 출력:
```
Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/your_username/stock-prediction
```

> 이 시점부터 그 노트북은 Kaggle 계정에 영구 등록됨. 이후 FastAPI에서 trigger만 하면 됨.

---

## 5. FastAPI 통합 — 트리거 서비스 작성

### 5-1. 새 서비스 파일

`app/services/ml_trigger_service.py` 신규 작성:

```python
"""
Kaggle API로 ML 예측 노트북 실행 트리거 + 완료 대기.
"""
import subprocess
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 설정
KAGGLE_USER = "your_kaggle_username"           # ← 본인 사용자명
KERNEL_SLUG = "stock-prediction"
KERNEL_REF = f"{KAGGLE_USER}/{KERNEL_SLUG}"

NOTEBOOK_DIR = Path(__file__).resolve().parents[2] / "kaggle_notebook"

POLL_INTERVAL_SEC = 10                          # 10초마다 상태 확인
MAX_WAIT_SEC = 900                              # 최대 15분 대기


def _run_kaggle_cmd(args: list[str]) -> tuple[int, str, str]:
    """kaggle CLI 실행. (returncode, stdout, stderr) 반환"""
    proc = subprocess.run(
        ["kaggle"] + args,
        capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


def push_kernel() -> bool:
    """노트북 push (= 새 버전 + 실행 트리거)"""
    if not NOTEBOOK_DIR.exists():
        logger.error(f"노트북 폴더가 없음: {NOTEBOOK_DIR}")
        return False

    rc, out, err = _run_kaggle_cmd(["kernels", "push", "-p", str(NOTEBOOK_DIR)])
    if rc != 0:
        logger.error(f"Kaggle push 실패 (rc={rc}): {err.strip()}")
        return False

    logger.info(f"Kaggle 노트북 push 성공: {out.strip()}")
    return True


def get_status() -> str:
    """현재 실행 상태 조회. 'complete' / 'running' / 'queued' / 'error' / 'cancel*' / 'unknown'"""
    rc, out, err = _run_kaggle_cmd(["kernels", "status", KERNEL_REF])
    if rc != 0:
        logger.warning(f"status 조회 실패: {err.strip()}")
        return "unknown"

    text = out.lower()
    for state in ["complete", "error", "cancel_acknowledged", "cancel_requested",
                  "running", "queued"]:
        if state in text:
            return state
    return "unknown"


def trigger_and_wait() -> bool:
    """노트북 실행 + 완료까지 대기. 성공 True, 실패 False"""
    # 1) push (= 트리거)
    if not push_kernel():
        return False

    logger.info("Kaggle 실행 시작 - 완료 대기 중 (최대 15분)")
    start = time.time()
    last_status = None

    # 2) 폴링
    while True:
        elapsed = time.time() - start
        if elapsed > MAX_WAIT_SEC:
            logger.error(f"Kaggle 실행 타임아웃 ({MAX_WAIT_SEC}초)")
            return False

        time.sleep(POLL_INTERVAL_SEC)
        status = get_status()

        if status != last_status:
            logger.info(f"  [{int(elapsed)}s] 상태: {status}")
            last_status = status

        if status == "complete":
            logger.info(f"Kaggle 실행 완료 ({int(elapsed)}초 소요)")
            return True
        if status in ("error", "cancel_acknowledged", "cancel_requested"):
            logger.error(f"Kaggle 실행 실패: {status}")
            return False
        # running / queued / unknown → 계속 대기
```

### 5-2. 호출 위치: 새 daily pipeline 함수

`app/utils/scheduler.py` 에 추가:

```python
def _run_full_daily_pipeline():
    """
    매일 한 번 실행되는 전체 파이프라인:
      1. Kaggle ML 학습 (~7분)
      2. 기술 지표 생성
      3. 뉴스 감성 분석

    LLM 검토 + 매수는 별도 _run_auto_buy() 가 NY 10:30 ET에 자동 처리.
    """
    logger.info("===== Daily ML pipeline 시작 =====")
    pipeline_start = time.time()

    # Step 1: ML 예측 (Kaggle)
    from app.services.ml_trigger_service import trigger_and_wait
    if not trigger_and_wait():
        logger.error("ML 실행 실패 - 파이프라인 중단")
        # TODO: Slack/이메일 알림
        return

    # Step 2: 기술 지표
    try:
        logger.info("기술 지표 생성 시작")
        from app.services.stock_recommendation_service import StockRecommendationService
        service = StockRecommendationService()
        tech_result = service.generate_technical_recommendations()
        logger.info(f"기술 지표 완료: {tech_result['message']}")
    except Exception as e:
        logger.error(f"기술 지표 실패: {e}", exc_info=True)
        return

    # Step 3: 감성 분석
    try:
        logger.info("뉴스 감성 분석 시작")
        sentiment_result = service.fetch_and_store_sentiment_for_recommendations()
        logger.info(f"감성 분석 완료: {sentiment_result['message']}")
    except Exception as e:
        logger.error(f"감성 분석 실패: {e}", exc_info=True)
        return

    elapsed = time.time() - pipeline_start
    logger.info(f"===== Daily ML pipeline 완료 (총 {int(elapsed)}초) =====")
```

### 5-3. 스케줄 등록

기존 `start_economic_data_scheduler()` 처럼 등록 함수 추가:

```python
ml_pipeline_scheduler_running = False

def start_ml_pipeline_scheduler():
    """ML 파이프라인 스케줄러 시작 (매일 KST 22:00)"""
    global ml_pipeline_scheduler_running
    if ml_pipeline_scheduler_running:
        logger.warning("ML 파이프라인 스케줄러가 이미 실행 중")
        return False

    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_full_daily_pipeline']:
        schedule.cancel_job(job)

    schedule.every().day.at("22:00").do(_run_full_daily_pipeline)
    ml_pipeline_scheduler_running = True
    logger.info("ML 파이프라인 스케줄러 시작 (매일 KST 22:00)")
    return True


def stop_ml_pipeline_scheduler():
    global ml_pipeline_scheduler_running
    for job in [j for j in schedule.jobs if j.job_func.__name__ == '_run_full_daily_pipeline']:
        schedule.cancel_job(job)
    ml_pipeline_scheduler_running = False
    logger.info("ML 파이프라인 스케줄러 중지")
    return True
```

### 5-4. 앱 시작 시 자동 시작

`app/main.py` 의 `lifespan` 함수에 한 줄 추가:

```python
from app.utils.scheduler import start_ml_pipeline_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_economic_data_scheduler()
    start_scheduler()           # 기존 매수
    start_sell_scheduler()      # 기존 매도
    start_ml_pipeline_scheduler()  # ★ 신규
    ...
```

---

## 6. 시간 흐름 한눈에 보기

```
KST 22:00 ──────────── ML 파이프라인 시작 (★)
                       │
                       │ ① Kaggle push (1초)
                       ▼
                       Kaggle 큐 대기 (보통 30초~2분)
                       │
                       ▼
                       Kaggle GPU에서 학습 (5~7분)
                       │
                       ▼
                       Supabase에 INSERT
                       │
KST 22:08 ─────────────┼ status='complete' 감지
                       │
                       │ ② generate_technical_recommendations() (~30초)
                       ▼
                       stock_recommendations 테이블 갱신
                       │
                       │ ③ fetch_and_store_sentiment_for_recommendations() (~2분)
                       ▼
                       ticker_sentiment_analysis 테이블 갱신

KST 22:11 ─────────────┴ Daily ML pipeline 완료
                          (LLM 검토 + 매수는 별도 NY 10:30 ET 트리거)
```

---

## 7. 에러 처리 + 재시도 패턴

### 7-1. 푸시 자체 실패 (네트워크 등)

```python
def push_kernel_with_retry(max_retries: int = 3) -> bool:
    for attempt in range(max_retries):
        if push_kernel():
            return True
        wait = 30 * (attempt + 1)  # 30s → 60s → 90s
        logger.warning(f"push 재시도 {attempt + 2}/{max_retries} ({wait}초 후)")
        time.sleep(wait)
    return False
```

### 7-2. Kaggle GPU 큐가 매우 길어진 경우

`MAX_WAIT_SEC` 를 1800(30분)까지 늘리거나, 다음과 같이 분리:

```python
# 큐 대기와 실행 대기를 다르게 타임아웃
QUEUE_TIMEOUT = 600    # 10분 안에 큐에서 빠져나와야
RUN_TIMEOUT = 900      # 큐 통과 후 15분 안에 끝나야

# (트래킹 로직은 status 변화 감지로 구현)
```

### 7-3. Status가 `complete` 인데 DB 갱신이 안 됨 (로직 실패)

`predict.py` 자체에서 에러가 나면 `status='complete'` 가 아니라 `status='error'` 가 나와야 정상.
하지만 try/except 로 감싸서 에러를 삼키면 표면상 complete로 끝남.
→ **Supabase의 `stock_analysis_results.created_at` 이 24시간 이상이면 매수 차단** 가드 추가 (이미 `07_자동화_방안.md` 에 제안됨).

```python
# app/services/stock_recommendation_service.py:get_stock_recommendations()
from datetime import datetime, timedelta

latest = pd.to_datetime(df['created_at']).max()
if latest < datetime.utcnow() - timedelta(hours=24):
    return {
        "message": f"ML 데이터가 24시간 이상 오래됨 ({latest}) - 매수 차단",
        "recommendations": []
    }
```

---

## 8. Kaggle UI Schedule vs API 비교

| 항목 | UI Schedule | **API (이 가이드)** |
|---|---|---|
| 설정 위치 | Kaggle 웹사이트 | FastAPI 코드 |
| 단계 체이닝 | ❌ 각 단계 별도 시간 | ✅ 순차 보장 |
| 실패 시 재시도 | ❌ | ✅ |
| 코드 버전 관리 | ❌ (UI 수정) | ✅ (git) |
| 알림 통합 | ❌ | ✅ (Slack/Discord 추가 가능) |
| 진행 상황 모니터링 | ❌ (Kaggle 사이트만) | ✅ (FastAPI 로그) |
| 즉시 실행 (수동 트리거) | ❌ | ✅ (FastAPI 라우트로 호출) |
| Cold start 시간 | 같음 (~30s~2분) | 같음 |

---

## 9. 수동 트리거용 API 라우트 (선택)

급할 때 또는 디버깅용으로 즉시 실행 라우트:

```python
# app/api/routes/ml_trigger.py
from fastapi import APIRouter, BackgroundTasks
from app.utils.scheduler import _run_full_daily_pipeline

router = APIRouter(prefix="/ml", tags=["ml"])

@router.post("/trigger-pipeline")
def trigger_pipeline(background_tasks: BackgroundTasks):
    """ML 파이프라인 즉시 실행 (15분 가량 걸림 - 백그라운드로 진행)"""
    background_tasks.add_task(_run_full_daily_pipeline)
    return {"message": "ML 파이프라인 시작됨. 로그를 확인하세요."}


@router.get("/kaggle-status")
def kaggle_status():
    """현재 Kaggle 노트북 상태 조회"""
    from app.services.ml_trigger_service import get_status
    return {"status": get_status()}
```

`app/api/api.py` 에 등록:
```python
from app.api.routes import ml_trigger
api_router.include_router(ml_trigger.router)
```

---

## 10. Kaggle 한계 + 함정

### 10-1. 무료 GPU 시간 제한

- 주당 **30 GPU 시간** 제한
- 매일 7분 × 7일 = 약 50분/주 → **여유 매우 충분**
- 단, 학습 시간이 30분으로 늘어나면 주당 한계 가능 (3.5시간/주)

### 10-2. 큐 대기 시간 변동

- 무료 큐는 GPU 가용성에 따라 **30초 ~ 5분** 차이
- Daily 22:00 같은 한가한 시간 추천 (장 시작 23:30 KST 전 충분한 여유)

### 10-3. 노트북 versioning 누적

`kaggle kernels push` 매번 호출 시 새 버전 생성:
```
v1, v2, v3, ... v365 (1년 후)
```
Kaggle은 자동 정리 안 함. 수동으로 옛 버전 삭제 가능 (UI에서).

### 10-4. API rate limit

- 분당 약 **30회** 제한 (비공식)
- 우리는 폴링 10초 간격 → 분당 6회 → 안전

### 10-5. `kernel-metadata.json` ID 충돌

- Kaggle 전체에서 unique 해야 함
- `your_username/stock-prediction` 형식이라 보통 충돌 안 남
- 변경 시 새 노트북으로 분리됨 (기존 것 삭제 안 됨)

### 10-6. Internet 차단 노트북에서는 Supabase 못 씀

- `kernel-metadata.json` 의 `enable_internet: "true"` 필수
- 비공개 노트북은 Internet 사용 시 Kaggle 계정 인증 필요할 수 있음

---

## 11. 보안 점검

### 11-1. Kaggle API 키 보호

- `~/.kaggle/kaggle.json` **절대 git에 커밋 금지**
- `.gitignore` 확인:
  ```
  .kaggle/
  kaggle.json
  ```

### 11-2. Supabase 키 (Kaggle Secrets)

- 노트북 페이지 → **Add-ons → Secrets** 에 등록 (UI에서 마스킹됨)
- `kernel-metadata.json` 에 적으면 **공개됨** → 절대 적지 말 것

### 11-3. 노트북 비공개 설정

- `kernel-metadata.json` 의 `is_private: "true"` 필수
- Public이면 코드 + Supabase URL 노출됨

---

## 12. 모니터링 + 알림 (확장)

### 12-1. Slack Webhook 통합 예시

```python
# app/services/notification_service.py
import requests
from app.core.config import settings

def notify_slack(message: str, level: str = "info"):
    webhook = settings.SLACK_WEBHOOK_URL
    if not webhook:
        return
    color = {"info": "#36a64f", "warn": "#ff9800", "error": "#ff0000"}.get(level, "#888")
    requests.post(webhook, json={
        "attachments": [{"color": color, "text": message}]
    })
```

`_run_full_daily_pipeline()` 끝에 추가:

```python
notify_slack(f"✅ ML 파이프라인 완료 ({int(elapsed)}초)", "info")
# 실패 시:
notify_slack(f"❌ ML 실행 실패: {error_msg}", "error")
```

### 12-2. Daily Report

매수 후 다음 날 아침에 어제 결과 요약 발송:

```python
def _send_daily_report():
    # trade_records 어제 거래 + 보유 종목 평가손익 집계
    # → Slack/이메일 발송
    pass

schedule.every().day.at("08:00").do(_send_daily_report)
```

---

## 13. 비용 정리

| 항목 | 비용 |
|---|---|
| Kaggle 계정 + API | **₩0** |
| Kaggle GPU 시간 (주 30시간 무료) | **₩0** |
| Supabase | 기존 그대로 |
| FastAPI 서버 | 기존 그대로 |
| **합계 추가 비용** | **₩0** |

---

## 14. 마이그레이션 체크리스트

### Phase A: Kaggle 노트북 셋업 (1시간)
- [ ] Kaggle 계정 가입
- [ ] API 토큰 발급 (`kaggle.json`)
- [ ] 서버에 `~/.kaggle/kaggle.json` 배치
- [ ] `pip install kaggle` 후 `kaggle kernels list -m` 인증 확인
- [ ] `kaggle_notebook/kernel-metadata.json` 작성 (본인 username)
- [ ] `kaggle_notebook/predict.py` 작성 (Supabase 키 → 환경변수)
- [ ] Kaggle Secrets에 SUPABASE_URL/KEY 등록
- [ ] `kaggle kernels push -p kaggle_notebook` 첫 푸시
- [ ] Kaggle 웹에서 실행 결과 확인 (DB에 잘 들어갔는지)

### Phase B: FastAPI 통합 (1시간)
- [ ] `app/services/ml_trigger_service.py` 작성
- [ ] `app/utils/scheduler.py` 에 `_run_full_daily_pipeline` 추가
- [ ] `start_ml_pipeline_scheduler()` 등록 함수 추가
- [ ] `app/main.py` lifespan에 `start_ml_pipeline_scheduler()` 호출
- [ ] `requirements.txt` 에 `kaggle>=1.6.0` 추가
- [ ] 로컬에서 수동 트리거 테스트 (`/ml/trigger-pipeline` 라우트)

### Phase C: 안정화 (30분)
- [ ] `get_stock_recommendations()` 에 24시간 신선도 가드 추가
- [ ] Slack/이메일 알림 통합 (선택)
- [ ] 일주일 운영 후 큐 대기 시간 패턴 확인 → 22:00 → 적절히 조정

---

## 15. 자주 발생하는 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `403 Forbidden` push 시 | API 키 만료/오류 | Kaggle Settings에서 새 토큰 발급 |
| `kernel-metadata.json` not found | 폴더 경로 잘못 | `-p` 절대경로로 지정 |
| `status='running'` 30분째 | GPU 큐 매우 길거나 무한 루프 | Kaggle 웹에서 수동 cancel + 코드 점검 |
| Supabase 401 | Kaggle Secrets 미등록 | 노트북 → Add-ons → Secrets 확인 |
| `id` 중복 에러 | 다른 사람 username 사용 | 본인 username으로 변경 |
| GPU 안 잡힘 | `enable_gpu` 빠짐 | `kernel-metadata.json` 수정 후 push |

---

## 16. 결론: 왜 이 방식이 우리에게 최선인가

1. **무료** (Kaggle GPU 주 30시간 한도 내에서는 영구 무료)
2. **우리 시스템의 일부로 통합** (FastAPI 로그, 스케줄러, 알림 모두 일원화)
3. **순차 실행 보장** (ML 끝나야 다음 단계 시작)
4. **재시도/타임아웃 등 운영 로직 추가 가능**
5. **버전 관리** (`predict.py`가 git 안에 있음)
6. **즉시 수동 트리거 가능** (`/ml/trigger-pipeline` 라우트)

> **약 2.5시간 작업으로 매일 4단계 수동 작업이 완전히 사라집니다.**

---

## 17. 코드 위치 인덱스

| 파일 | 역할 | 신규/수정 |
|---|---|---|
| `kaggle_notebook/kernel-metadata.json` | Kaggle 노트북 설정 | 🆕 신규 |
| `kaggle_notebook/predict.py` | predict_colab.py 정리본 | 🆕 신규 (기존 복사) |
| `app/services/ml_trigger_service.py` | Kaggle CLI 호출 + 폴링 | 🆕 신규 |
| `app/utils/scheduler.py` | `_run_full_daily_pipeline`, `start_ml_pipeline_scheduler` | ✏️ 수정 |
| `app/main.py` | lifespan 함수에 스케줄러 시작 호출 | ✏️ 수정 |
| `app/services/stock_recommendation_service.py` | 24시간 신선도 가드 | ✏️ 수정 |
| `app/api/routes/ml_trigger.py` | 수동 트리거 라우트 | 🆕 신규 (선택) |
| `app/api/api.py` | ml_trigger 라우터 등록 | ✏️ 수정 (선택) |
| `requirements.txt` | `kaggle>=1.6.0` | ✏️ 수정 |
| `.gitignore` | `.kaggle/`, `kaggle.json` | ✏️ 수정 |

---

## 18. 관련 문서

- `07_자동화_방안.md` — 전체 자동화 전략 (이 문서는 그 중 Phase 2의 상세본)
- `05_ML_예측_모델_상세.md` — predict_colab.py 코드 상세 해설
- `01_시스템_개요.md` — 전체 시스템 구조

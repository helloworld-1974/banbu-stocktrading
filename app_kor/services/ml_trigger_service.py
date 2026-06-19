"""
Kaggle API로 ML 예측 노트북을 트리거하는 서비스 (한국주식판).

미국판과 동일하나, 한국 전용 커널(settings.KAGGLE_KERNEL_SLUG = stock-prediction-kor)과
노트북 폴더(settings.KAGGLE_NOTEBOOK_DIR = kaggle_notebook_kor)를 사용.

predict.py 를 .ipynb 로 변환하면서 .env 의 SUPABASE_URL/KEY 를 첫 셀에 주입.
결과 .ipynb 는 secrets 포함 → .gitignore 필수.
"""
import json
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Tuple, Optional

from app_kor.core.config import settings

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 10
MAX_WAIT_SEC = 900  # 15분

TERMINAL_OK = {"complete"}
TERMINAL_ERR = {"error", "cancel_acknowledged", "cancel_requested"}


def _kernel_ref() -> str:
    if not settings.KAGGLE_USERNAME:
        raise RuntimeError("KAGGLE_USERNAME 이 .env 에 설정되지 않았습니다 (실제 Kaggle username 사용)")
    return f"{settings.KAGGLE_USERNAME}/{settings.KAGGLE_KERNEL_SLUG}"


def _notebook_dir() -> Path:
    p = Path(settings.KAGGLE_NOTEBOOK_DIR)
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        p = project_root / p
    return p


def _kaggle_env() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if settings.KAGGLE_API_TOKEN:
        env["KAGGLE_API_TOKEN"] = settings.KAGGLE_API_TOKEN
        if settings.KAGGLE_USERNAME:
            env["KAGGLE_USERNAME"] = settings.KAGGLE_USERNAME
        return env
    if settings.KAGGLE_USERNAME and settings.KAGGLE_KEY:
        env["KAGGLE_USERNAME"] = settings.KAGGLE_USERNAME
        env["KAGGLE_KEY"] = settings.KAGGLE_KEY
        return env
    raise RuntimeError("Kaggle 인증 정보가 .env 에 없습니다. (KAGGLE_API_TOKEN 또는 KAGGLE_USERNAME+KAGGLE_KEY)")


def _run_kaggle_cmd(args: list, timeout: int = 60) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(["kaggle"] + args, capture_output=True, text=True, timeout=timeout, env=_kaggle_env())
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "kaggle CLI 미설치 (pip install kaggle 필요)"
    except subprocess.TimeoutExpired as e:
        return 124, "", f"kaggle 명령 타임아웃 ({timeout}초): {e}"


def check_auth() -> Tuple[bool, str]:
    rc, out, err = _run_kaggle_cmd(["kernels", "list", "-m", "--page-size", "1"])
    if rc == 0:
        return True, "Kaggle 인증 OK"
    return False, f"Kaggle 인증 실패 (rc={rc}): {err.strip() or out.strip()}"


def _build_ipynb_with_injected_secrets(py_path: Path, ipynb_path: Path) -> None:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 가 .env 에 없습니다. predict.ipynb 주입 불가.")

    with open(py_path, "r", encoding="utf-8") as f:
        code = f.read()

    secret_cell = {
        "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
        "source": [
            "# AUTO-INJECTED by ml_trigger_service. Do NOT edit / do NOT commit.\n",
            "import os\n",
            f"os.environ['SUPABASE_URL'] = {settings.SUPABASE_URL!r}\n",
            f"os.environ['SUPABASE_KEY'] = {settings.SUPABASE_KEY!r}\n",
        ],
    }
    main_cell = {
        "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
        "source": code.splitlines(keepends=True),
    }
    nb = {
        "cells": [secret_cell, main_cell],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }
    with open(ipynb_path, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)


def push_kernel() -> Tuple[bool, str]:
    nb_dir = _notebook_dir()
    if not nb_dir.exists():
        msg = f"노트북 폴더가 없음: {nb_dir} (kernel-metadata.json + predict.py 필요)"
        logger.error(msg)
        return False, msg
    if not (nb_dir / "kernel-metadata.json").exists():
        msg = f"kernel-metadata.json 없음: {nb_dir}"
        logger.error(msg)
        return False, msg

    py_path = nb_dir / "predict.py"
    ipynb_path = nb_dir / "predict.ipynb"
    if not py_path.exists():
        msg = f"predict.py 없음: {py_path}"
        logger.error(msg)
        return False, msg

    try:
        _build_ipynb_with_injected_secrets(py_path, ipynb_path)
        logger.info("predict.ipynb 재생성 완료 (secrets 주입됨)")
    except Exception as e:
        msg = f"ipynb 생성 실패: {e}"
        logger.error(msg, exc_info=True)
        return False, msg

    rc, out, err = _run_kaggle_cmd(["kernels", "push", "-p", str(nb_dir)], timeout=120)
    if rc != 0:
        msg = f"Kaggle push 실패 (rc={rc}): {err.strip() or out.strip()}"
        logger.error(msg)
        return False, msg
    out_msg = out.strip()
    logger.info(f"Kaggle 노트북 push 성공: {out_msg}")
    return True, out_msg


def get_status() -> str:
    rc, out, err = _run_kaggle_cmd(["kernels", "status", _kernel_ref()])
    if rc != 0:
        logger.warning(f"status 조회 실패 (rc={rc}): {err.strip() or out.strip()}")
        return "unknown"
    text = (out + " " + err).lower()
    for state in ("complete", "error", "cancel_acknowledged", "cancel_requested", "running", "queued"):
        if state in text:
            return state
    return "unknown"


def trigger_and_wait(poll_interval: int = POLL_INTERVAL_SEC, max_wait: int = MAX_WAIT_SEC) -> Tuple[bool, str, dict]:
    start = time.time()
    pushed, push_msg = push_kernel()
    if not pushed:
        return False, push_msg, {"elapsed_sec": 0, "final_status": "push_failed", "push_output": push_msg}

    logger.info(f"Kaggle 실행 시작 - 완료 대기 중 (최대 {max_wait}초)")
    last_status: Optional[str] = None
    while True:
        elapsed = int(time.time() - start)
        if elapsed > max_wait:
            msg = f"Kaggle 실행 타임아웃 ({max_wait}초)"
            logger.error(msg)
            return False, msg, {"elapsed_sec": elapsed, "final_status": "timeout", "push_output": push_msg}

        time.sleep(poll_interval)
        status = get_status()
        if status != last_status:
            logger.info(f"  [{elapsed}s] 상태: {status}")
            last_status = status
        if status in TERMINAL_OK:
            msg = f"Kaggle 실행 완료 ({elapsed}초)"
            logger.info(msg)
            return True, msg, {"elapsed_sec": elapsed, "final_status": status, "push_output": push_msg}
        if status in TERMINAL_ERR:
            msg = f"Kaggle 실행 실패: {status}"
            logger.error(msg)
            return False, msg, {"elapsed_sec": elapsed, "final_status": status, "push_output": push_msg}

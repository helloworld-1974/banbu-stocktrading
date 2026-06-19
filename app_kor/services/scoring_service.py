"""
매수 후보 점수 산출 모듈 (v1 + v2). — 한국주식판

시장 무관한 순수 점수 로직이라 미국판과 동일하다 (candidate dict 만 받음).

설계:
  - v1 (raw weighted sum):  팩터별 절대값 점수의 가중합 (구버전)
  - v2 (cross-sectional z-score): 후보군 내 표준화 후 가중합 (개선판)
  - score_and_filter():     orchestrator — USE_SCORING_V2 에 따라 한 가지만 실행

원칙:
  - 활성 버전만 계산.
  - 양쪽 모두 결과를 candidate["composite_score"] 단일 키로 통일.
  - candidate["scoring_version"] = "v1" or "v2".
"""
from typing import List, Optional

import numpy as np


# ══════════════════════════════════════════════════════════════════
# 공유: 사전 필터 (v1, v2 공통)
# ══════════════════════════════════════════════════════════════════

def apply_prefilters(item: dict) -> bool:
    """매수 후보 사전 필터. RSI>80 하드블록 + 기술 신호 2개 이상."""
    rsi = item.get("rsi", 50)
    if rsi > 80:
        return False

    rsi_buy = rsi <= 65
    tech_signals = [
        bool(item.get("golden_cross")),
        rsi_buy,
        bool(item.get("macd_buy_signal")),
    ]
    if sum(tech_signals) < 2:
        return False

    return True


# ══════════════════════════════════════════════════════════════════
# v1: Raw Weighted Sum (구버전)
# ══════════════════════════════════════════════════════════════════

V1_W_RISE = 0.25
V1_W_TECH = 0.25
V1_W_SENT = 0.20
V1_W_VOL = 0.15
V1_W_ADX = 0.10
V1_W_VIX = 0.05

V1_THRESHOLD = 0.3


def compute_v1(item: dict, vix_value: Optional[float]) -> None:
    """v1 점수 in-place 계산."""
    rsi = item["rsi"]
    rsi_buy = rsi <= 65
    raw_sentiment = item.get("sentiment_score") if item.get("sentiment_score") is not None else 0.0

    rp = item["rise_probability"]
    if rp < 3:
        rise_score = 0.2
    elif rp < 5:
        rise_score = 0.4
    elif rp < 8:
        rise_score = 0.6
    elif rp < 12:
        rise_score = 0.8
    else:
        rise_score = 1.0

    tech_count = (
        1.5 * bool(item["golden_cross"])
        + 1.0 * rsi_buy
        + 1.0 * bool(item["macd_buy_signal"])
    )
    tech_score = tech_count / 3.5

    sentiment_score = (raw_sentiment + 1) / 2

    vr = item.get("volume_ratio")
    if vr is None:
        volume_score = 0.0
    elif vr < 0.5:
        volume_score = -0.5
    elif vr < 1.0:
        volume_score = 0.0
    elif vr < 1.5:
        volume_score = 0.3
    else:
        volume_score = 0.6

    adx = item.get("adx")
    if adx is None:
        adx_score = 0.0
    elif adx > 25:
        adx_score = 0.4
    elif adx >= 20:
        adx_score = 0.0
    else:
        adx_score = -0.3

    if vix_value is None:
        vix_score = 0.0
    elif vix_value < 20:
        vix_score = 0.0
    elif vix_value < 30:
        vix_score = -0.2
    else:
        vix_score = -0.5

    composite = (
        V1_W_RISE * rise_score
        + V1_W_TECH * tech_score
        + V1_W_SENT * sentiment_score
        + V1_W_VOL * volume_score
        + V1_W_ADX * adx_score
        + V1_W_VIX * vix_score
    )

    item["rise_score"] = round(rise_score, 2)
    item["tech_score"] = round(tech_score, 2)
    item["sentiment_score_norm"] = round(sentiment_score, 2)
    item["volume_score"] = round(volume_score, 2)
    item["adx_score"] = round(adx_score, 2)
    item["vix_score"] = round(vix_score, 2)
    item["vix_value"] = vix_value
    item["composite_score"] = round(composite, 4)
    item["scoring_version"] = "v1"


def get_v1_threshold(vix_value: Optional[float] = None) -> float:
    return V1_THRESHOLD


# ══════════════════════════════════════════════════════════════════
# v2: Cross-Sectional Z-Score (개선판)
# ══════════════════════════════════════════════════════════════════

V2_W_RISE = 0.20
V2_W_TECH = 0.30
V2_W_VOL = 0.20
V2_W_ADX = 0.20
V2_W_SENT = 0.10

WINSOR_LIMIT = 3.0
V2_BASE_THRESHOLD = 0.4


def cross_sectional_zscore(values: List[Optional[float]]) -> List[float]:
    """후보군 내 z-score 정규화. None 은 평균(0) 처리, ±3σ winsorize."""
    arr = np.array(
        [float(v) if v is not None else np.nan for v in values],
        dtype=float,
    )
    mean = np.nanmean(arr)
    std = np.nanstd(arr)
    if not np.isfinite(std) or std < 1e-9:
        return [0.0] * len(values)
    z = (arr - mean) / std
    z = np.nan_to_num(z, nan=0.0, posinf=WINSOR_LIMIT, neginf=-WINSOR_LIMIT)
    z = np.clip(z, -WINSOR_LIMIT, WINSOR_LIMIT)
    return [float(x) for x in z]


def compute_v2(candidates: List[dict], vix_value: Optional[float]) -> None:
    """v2 점수 in-place 계산 (cross-sectional)."""
    n = len(candidates)
    if n == 0:
        return

    rise_raw = [c.get("rise_probability", 0) or 0 for c in candidates]
    rsi_raw = [c.get("rsi", 50) or 50 for c in candidates]
    macd_diff_raw = [(c.get("macd", 0) or 0) - (c.get("signal", 0) or 0) for c in candidates]
    sma_diff_raw = [((c.get("sma20", 0) or 0) / max(c.get("sma50", 1) or 1, 1e-9) - 1) for c in candidates]
    sent_raw = [c.get("sentiment_score") for c in candidates]
    vol_raw = [c.get("volume_ratio") for c in candidates]
    adx_raw = [c.get("adx") for c in candidates]

    z_rise = cross_sectional_zscore(rise_raw)
    z_rsi = cross_sectional_zscore(rsi_raw)
    z_macd = cross_sectional_zscore(macd_diff_raw)
    z_sma = cross_sectional_zscore(sma_diff_raw)
    z_sent = cross_sectional_zscore(sent_raw)
    z_vol = cross_sectional_zscore(vol_raw)
    z_adx = cross_sectional_zscore(adx_raw)

    z_tech = [(z_macd[i] + z_sma[i] + z_rsi[i]) / 3.0 for i in range(n)]

    for i, c in enumerate(candidates):
        composite = (
            V2_W_RISE * z_rise[i]
            + V2_W_TECH * z_tech[i]
            + V2_W_SENT * z_sent[i]
            + V2_W_VOL * z_vol[i]
            + V2_W_ADX * z_adx[i]
        )
        c["composite_score"] = round(composite, 4)
        c["v2_factors"] = {
            "z_rise": round(z_rise[i], 3),
            "z_tech": round(z_tech[i], 3),
            "z_macd": round(z_macd[i], 3),
            "z_sma": round(z_sma[i], 3),
            "z_rsi": round(z_rsi[i], 3),
            "z_sent": round(z_sent[i], 3),
            "z_vol": round(z_vol[i], 3),
            "z_adx": round(z_adx[i], 3),
        }
        c["vix_value"] = vix_value
        c["scoring_version"] = "v2"


def get_v2_threshold(vix_value: Optional[float]) -> float:
    """VIX 기반 적응형 임계값 (z-score 기준)."""
    if vix_value is None or vix_value < 20:
        return V2_BASE_THRESHOLD
    if vix_value < 25:
        return V2_BASE_THRESHOLD + 0.05
    if vix_value < 30:
        return V2_BASE_THRESHOLD + 0.15
    return V2_BASE_THRESHOLD + 0.30


# ══════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════

def score_and_filter(candidates: List[dict], vix_value: Optional[float], use_v2: bool) -> List[dict]:
    """매수 후보 채점 + 필터링 + 정렬."""
    passed = [c for c in candidates if apply_prefilters(c)]
    if not passed:
        return []

    if use_v2:
        compute_v2(passed, vix_value)
        threshold = get_v2_threshold(vix_value)
    else:
        for c in passed:
            compute_v1(c, vix_value)
        threshold = get_v1_threshold(vix_value)

    final = [c for c in passed if c["composite_score"] >= threshold]
    final.sort(key=lambda x: x["composite_score"], reverse=True)
    return final

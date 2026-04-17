"""
주가 예측 서비스 (Transformer 모델)
predict_local.py 로직을 서비스로 변환
"""
import os
import time
import threading
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import tensorflow as tf
from tensorflow.keras.models import Model, load_model
from tensorflow.keras.layers import (
    Input, Dense, Dropout, LayerNormalization, MultiHeadAttention, Add, GlobalAveragePooling1D
)
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
import joblib
import logging

from app.db.supabase import supabase

logger = logging.getLogger('predict_service')

# ============================================================
# 설정값
# ============================================================
LOOKBACK = 90
FORECAST_HORIZON = 14
EPOCHS = 100
BATCH_SIZE = 32
LEARNING_RATE = 0.0001
PATIENCE = 10
TRAIN_RATIO = 0.8

MODEL_DIR = "saved_models"
MODEL_PATH = os.path.join(MODEL_DIR, "transformer_stock_model.keras")

TARGET_COLUMNS = [
    '애플', '마이크로소프트', '아마존', '구글 A', '구글 C', '메타',
    '테슬라', '엔비디아', '코스트코', '넷플릭스', '페이팔', '인텔', '시스코', '컴캐스트',
    '펩시코', '암젠', '허니웰 인터내셔널', '스타벅스', '몬델리즈', '마이크론', '브로드컴',
    '어도비', '텍사스 인스트루먼트', 'AMD', '어플라이드 머티리얼즈', 'S&P 500 ETF', 'QQQ ETF'
]

ECONOMIC_FEATURES = [
    '10년 기대 인플레이션율', '장단기 금리차', '기준금리', '미시간대 소비자 심리지수',
    '실업률', '2년 만기 미국 국채 수익률', '10년 만기 미국 국채 수익률', '금융스트레스지수',
    '개인 소비 지출', '소비자 물가지수', '5년 변동금리 모기지', '미국 달러 환율',
    '통화 공급량 M2', '가계 부채 비율', 'GDP 성장률', '나스닥 종합지수', 'S&P 500 지수',
    '금 가격', '달러 인덱스', '나스닥 100',
    'S&P 500 ETF', 'QQQ ETF', '러셀 2000 ETF', '다우 존스 ETF', 'VIX 지수',
    '닛케이 225', '상해종합', '항셍', '영국 FTSE', '독일 DAX', '프랑스 CAC 40',
    '미국 전체 채권시장 ETF', 'TIPS ETF', '투자등급 회사채 ETF', '달러/엔', '달러/위안',
    '미국 리츠 ETF'
]


# ============================================================
# 상태 관리
# ============================================================
class PredictStatus:
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

_status = {
    "state": PredictStatus.IDLE,
    "message": "",
    "started_at": None,
    "completed_at": None,
    "progress": "",
    "error": None,
}
_lock = threading.Lock()


def get_status():
    with _lock:
        return dict(_status)


def _set_status(state, message="", progress="", error=None):
    with _lock:
        _status["state"] = state
        _status["message"] = message
        _status["progress"] = progress
        if state == PredictStatus.RUNNING and _status["started_at"] is None:
            _status["started_at"] = time.time()
        if state in (PredictStatus.COMPLETED, PredictStatus.FAILED):
            _status["completed_at"] = time.time()
        if error:
            _status["error"] = str(error)


def _reset_status():
    with _lock:
        _status["state"] = PredictStatus.IDLE
        _status["message"] = ""
        _status["started_at"] = None
        _status["completed_at"] = None
        _status["progress"] = ""
        _status["error"] = None


# ============================================================
# 데이터 로드
# ============================================================
def _get_all_data(table_name):
    all_data = []
    offset = 0
    limit = 1000
    while True:
        response = supabase.table(table_name).select("*").order("날짜", desc=False).limit(limit).offset(offset).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        offset += limit
    return all_data


def _load_data():
    all_data = _get_all_data("economic_and_stock_data")
    df = pd.DataFrame(all_data)
    df['날짜'] = pd.to_datetime(df['날짜'])
    df.sort_values(by='날짜', inplace=True)
    df.reset_index(drop=True, inplace=True)
    df = df.ffill().bfill()

    exclude_columns = ['날짜', 'id']
    numeric_columns = [col for col in df.columns if col not in exclude_columns]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')

    nan_ratios = df[numeric_columns].isna().mean()
    valid_columns = [col for col in numeric_columns if nan_ratios[col] < 1.0]
    df.dropna(subset=valid_columns, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ============================================================
# Transformer 모델
# ============================================================
def _transformer_encoder(inputs, num_heads, ff_dim, dropout=0.1):
    attention_output = MultiHeadAttention(num_heads=num_heads, key_dim=inputs.shape[-1])(inputs, inputs)
    attention_output = Dropout(dropout)(attention_output)
    attention_output = Add()([inputs, attention_output])
    attention_output = LayerNormalization(epsilon=1e-6)(attention_output)

    ffn = Dense(ff_dim, activation="relu")(attention_output)
    ffn = Dense(inputs.shape[-1])(ffn)
    ffn_output = Dropout(dropout)(ffn)
    ffn_output = Add()([attention_output, ffn_output])
    ffn_output = LayerNormalization(epsilon=1e-6)(ffn_output)
    return ffn_output


def _build_model(stock_shape, econ_shape, target_size):
    stock_inputs = Input(shape=stock_shape)
    stock_encoded = stock_inputs
    for _ in range(4):
        stock_encoded = _transformer_encoder(stock_encoded, num_heads=8, ff_dim=256)
    stock_encoded = Dense(64, activation="relu")(stock_encoded)

    econ_inputs = Input(shape=econ_shape)
    econ_encoded = econ_inputs
    for _ in range(4):
        econ_encoded = _transformer_encoder(econ_encoded, num_heads=8, ff_dim=256)
    econ_encoded = Dense(64, activation="relu")(econ_encoded)

    merged = Add()([stock_encoded, econ_encoded])
    merged = Dense(128, activation="relu")(merged)
    merged = Dropout(0.2)(merged)
    merged = GlobalAveragePooling1D()(merged)
    outputs = Dense(target_size)(merged)

    return Model(inputs=[stock_inputs, econ_inputs], outputs=outputs)


# ============================================================
# 평가/분석
# ============================================================
def _evaluate_predictions(data, split_index=None):
    if split_index is not None:
        data = data.iloc[split_index:].reset_index(drop=True)

    metrics = []
    for col in TARGET_COLUMNS:
        predicted_col = f'{col}_Predicted'
        actual_col = f'{col}_Actual'
        if predicted_col not in data.columns or actual_col not in data.columns:
            continue

        predicted = data[predicted_col]
        actual = data[actual_col].shift(-(FORECAST_HORIZON - 1))
        valid_idx = ~predicted.isna() & ~actual.isna()
        predicted, actual = predicted[valid_idx], actual[valid_idx]
        if len(predicted) == 0:
            continue

        mae = mean_absolute_error(actual, predicted)
        mape = (abs((actual - predicted) / actual).mean()) * 100

        metrics.append({
            'Stock': col,
            'MAE': round(mae, 4),
            'RMSE': round(mean_squared_error(actual, predicted) ** 0.5, 4),
            'MAPE (%)': round(mape, 4),
            'Accuracy (%)': round(100 - mape, 4)
        })
    return pd.DataFrame(metrics)


def _analyze_rise(data):
    last_row = data.iloc[-1]
    results = []
    for col in TARGET_COLUMNS:
        actual = last_row.get(f'{col}_Actual', np.nan)
        predicted = last_row.get(f'{col}_Predicted', np.nan)
        if pd.notna(actual) and pd.notna(predicted):
            rise_prob = ((predicted - actual) / actual) * 100
            results.append({
                'Stock': col,
                'Last Actual Price': actual,
                'Predicted Future Price': predicted,
                'Predicted Rise': predicted > actual,
                'Rise Probability (%)': rise_prob
            })
        else:
            results.append({'Stock': col, 'Last Actual Price': actual,
                          'Predicted Future Price': predicted,
                          'Predicted Rise': np.nan, 'Rise Probability (%)': np.nan})
    return pd.DataFrame(results)


def _generate_recommendation(row):
    rise_prob = row.get('Rise Probability (%)', 0)
    if pd.isna(rise_prob) or pd.isna(row.get('Predicted Rise', False)):
        return "No Data"
    if row.get('Predicted Rise') and rise_prob > 0:
        return "STRONG BUY" if rise_prob > 2 else "BUY"
    return "SELL"


def _generate_analysis(row):
    name = row['Stock']
    rise_prob = row.get('Rise Probability (%)', 0)
    if pd.isna(rise_prob) or pd.isna(row.get('Predicted Rise', False)):
        return f"{name}: Not enough data"
    if row.get('Predicted Rise'):
        return f"{name} is expected to rise by about {rise_prob:.2f}%. Consider buying or holding."
    return f"{name} is expected to fall by about {-rise_prob:.2f}%. A cautious approach is recommended."


# ============================================================
# DB 저장
# ============================================================
def _save_predictions(result_df):
    records = result_df.to_dict('records')
    supabase.table("predicted_stocks").delete().neq("id", 0).execute()
    for i in range(0, len(records), 100):
        supabase.table("predicted_stocks").insert(records[i:i+100]).execute()


def _save_analysis(result_df):
    records = result_df.to_dict('records')
    supabase.table("stock_analysis_results").delete().neq("id", 0).execute()
    for i in range(0, len(records), 100):
        supabase.table("stock_analysis_results").insert(records[i:i+100]).execute()


# ============================================================
# 메인 실행 로직
# ============================================================
def _run_prediction(skip_train: bool = False):
    """예측 실행 (동기)"""
    try:
        _set_status(PredictStatus.RUNNING, "데이터 로드 중", "1/5")
        data = _load_data()
        if data is None or data.empty:
            raise ValueError("DB에서 데이터를 가져오지 못했습니다.")
        logger.info(f"데이터 로드 완료: {data.shape[0]}일 x {data.shape[1]}컬럼")

        # 스케일링
        _set_status(PredictStatus.RUNNING, "데이터 스케일링", "2/5")
        train_size = int(len(data) * TRAIN_RATIO)
        train_data = data.iloc[:train_size]

        stock_scaler = MinMaxScaler()
        econ_scaler = MinMaxScaler()
        stock_scaler.fit(train_data[TARGET_COLUMNS])
        econ_scaler.fit(train_data[ECONOMIC_FEATURES])

        data_scaled = data.copy()
        data_scaled[TARGET_COLUMNS] = stock_scaler.transform(data[TARGET_COLUMNS])
        data_scaled[ECONOMIC_FEATURES] = econ_scaler.transform(data[ECONOMIC_FEATURES])

        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(stock_scaler, os.path.join(MODEL_DIR, "stock_scaler.pkl"))
        joblib.dump(econ_scaler, os.path.join(MODEL_DIR, "econ_scaler.pkl"))

        # 모델 학습 또는 로드
        if skip_train and os.path.exists(MODEL_PATH):
            _set_status(PredictStatus.RUNNING, "저장된 모델 로드", "3/5")
            model = load_model(MODEL_PATH)
            logger.info(f"모델 로드 완료: {MODEL_PATH}")
        else:
            _set_status(PredictStatus.RUNNING, "모델 학습 중", "3/5")
            last_train_index = train_size - FORECAST_HORIZON

            X_stock_train, X_econ_train, y_train = [], [], []
            for i in range(LOOKBACK, last_train_index):
                X_stock_train.append(data_scaled[TARGET_COLUMNS].iloc[i - LOOKBACK:i].values)
                X_econ_train.append(data_scaled[ECONOMIC_FEATURES].iloc[i - LOOKBACK:i].values)
                y_train.append(data_scaled[TARGET_COLUMNS].iloc[i + FORECAST_HORIZON - 1].values)

            X_stock_train = np.array(X_stock_train)
            X_econ_train = np.array(X_econ_train)
            y_train = np.array(y_train)

            stock_shape = (LOOKBACK, len(TARGET_COLUMNS))
            econ_shape = (LOOKBACK, len(ECONOMIC_FEATURES))
            model = _build_model(stock_shape, econ_shape, len(TARGET_COLUMNS))
            model.compile(optimizer=Adam(learning_rate=LEARNING_RATE), loss='mse', metrics=['mae'])

            early_stopping = EarlyStopping(monitor='val_loss', patience=PATIENCE, restore_best_weights=True, verbose=1)
            checkpoint = ModelCheckpoint(MODEL_PATH, monitor='val_loss', save_best_only=True, verbose=0)

            t_train = time.time()
            model.fit(
                [X_stock_train, X_econ_train], y_train,
                epochs=EPOCHS, batch_size=BATCH_SIZE,
                validation_split=0.15,
                callbacks=[early_stopping, checkpoint],
                verbose=0
            )
            logger.info(f"모델 학습 완료: {time.time() - t_train:.1f}초")

        # 예측
        _set_status(PredictStatus.RUNNING, "예측 수행 중", "4/5")
        X_stock_full, X_econ_full = [], []
        for i in range(LOOKBACK, len(data_scaled)):
            X_stock_full.append(data_scaled[TARGET_COLUMNS].iloc[i - LOOKBACK:i].to_numpy())
            X_econ_full.append(data_scaled[ECONOMIC_FEATURES].iloc[i - LOOKBACK:i].to_numpy())

        predicted_prices = model.predict([np.array(X_stock_full), np.array(X_econ_full)], verbose=0)
        predicted_actual = stock_scaler.inverse_transform(predicted_prices)

        pred_len = len(predicted_actual)
        today_dates = data['날짜'].iloc[LOOKBACK:LOOKBACK + pred_len].values
        actual_end = min(LOOKBACK + pred_len, len(data))
        actual_full = data[TARGET_COLUMNS].iloc[LOOKBACK:actual_end].values

        if actual_full.shape[0] < pred_len:
            nan_padding = np.full((pred_len - actual_full.shape[0], len(TARGET_COLUMNS)), np.nan)
            actual_full = np.vstack([actual_full, nan_padding])

        result_data = pd.DataFrame({'날짜': today_dates})
        for idx, col in enumerate(TARGET_COLUMNS):
            result_data[f'{col}_Predicted'] = predicted_actual[:, idx]
            result_data[f'{col}_Actual'] = actual_full[:, idx]

        result_data['날짜'] = pd.to_datetime(result_data['날짜'], errors='coerce').dt.strftime('%Y-%m-%d')

        # 저장 및 분석
        _set_status(PredictStatus.RUNNING, "결과 저장 및 분석", "5/5")
        _save_predictions(result_data)

        test_start = train_size - LOOKBACK
        eval_test = _evaluate_predictions(result_data, split_index=test_start)
        avg_acc = eval_test['Accuracy (%)'].mean() if not eval_test.empty else 0

        rise_results = _analyze_rise(result_data)
        final_results = pd.merge(eval_test, rise_results, on='Stock', how='outer')
        final_results = final_results.sort_values(by='Rise Probability (%)', ascending=False)
        final_results['Recommendation'] = final_results.apply(_generate_recommendation, axis=1)
        final_results['Analysis'] = final_results.apply(_generate_analysis, axis=1)

        column_order = [
            'Stock', 'MAE', 'RMSE', 'MAPE (%)', 'Accuracy (%)',
            'Last Actual Price', 'Predicted Future Price', 'Predicted Rise',
            'Rise Probability (%)', 'Recommendation', 'Analysis'
        ]
        final_results = final_results[[c for c in column_order if c in final_results.columns]]
        _save_analysis(final_results)

        _set_status(PredictStatus.COMPLETED, f"완료 (Test 평균 정확도: {avg_acc:.2f}%)")
        logger.info(f"예측 완료 - Test 평균 정확도: {avg_acc:.2f}%")

    except Exception as e:
        logger.error(f"예측 실패: {e}", exc_info=True)
        _set_status(PredictStatus.FAILED, error=str(e))


def run_prediction_async(skip_train: bool = False):
    """비동기 예측 실행 (백그라운드 스레드)"""
    status = get_status()
    if status["state"] == PredictStatus.RUNNING:
        return {"success": False, "message": "이미 예측이 실행 중입니다."}

    _reset_status()
    thread = threading.Thread(target=_run_prediction, args=(skip_train,), daemon=True)
    thread.start()
    return {"success": True, "message": "예측 작업이 시작되었습니다."}


def get_predictions():
    """predicted_stocks 테이블에서 최근 예측 결과 조회"""
    response = supabase.table("predicted_stocks").select("*").order("날짜", desc=True).limit(100).execute()
    return response.data


def get_analysis():
    """stock_analysis_results 테이블에서 분석 결과 조회"""
    response = supabase.table("stock_analysis_results").select("*").execute()
    return response.data

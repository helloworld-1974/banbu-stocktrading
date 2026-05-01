-- ============================================================
-- stock_analysis_results 테이블 스키마 정렬
-- ------------------------------------------------------------
-- predict_colab.py 가 INSERT 하는 컬럼셋과 일치시키기 위해
-- 사용하지 않는 컬럼 제거 + 누락 컬럼 추가 + Stock NOT NULL 해제
--
-- 적용 후 최종 스키마:
--   id, "Stock", "MAE", "MSE", "RMSE", "MAPE (%)", "Accuracy (%)",
--   "Last Actual Price", "Predicted Future Price", "Predicted Rise",
--   "Rise Probability (%)", "Recommendation", "Analysis", created_at
--
-- 사용법:
--   Supabase SQL Editor에서 이 파일을 그대로 실행하세요.
-- ============================================================

-- 1) 사용하지 않는 컬럼 제거 (구버전 잔재)
ALTER TABLE stock_analysis_results DROP COLUMN IF EXISTS "Predicted Change (%)";
ALTER TABLE stock_analysis_results DROP COLUMN IF EXISTS "Direction";
ALTER TABLE stock_analysis_results DROP COLUMN IF EXISTS "Analysis Date";
ALTER TABLE stock_analysis_results DROP COLUMN IF EXISTS "prediction_date";
ALTER TABLE stock_analysis_results DROP COLUMN IF EXISTS "actual_future_price";

-- 2) 누락된 컬럼 추가 (predict_colab.py 가 INSERT 하는 컬럼)
ALTER TABLE stock_analysis_results ADD COLUMN IF NOT EXISTS "Predicted Rise" BOOLEAN;
ALTER TABLE stock_analysis_results ADD COLUMN IF NOT EXISTS "Recommendation" TEXT;
ALTER TABLE stock_analysis_results ADD COLUMN IF NOT EXISTS "Analysis" TEXT;
ALTER TABLE stock_analysis_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

-- 3) "Stock" NOT NULL 제약 해제 (목표 스키마와 일치)
ALTER TABLE stock_analysis_results ALTER COLUMN "Stock" DROP NOT NULL;

-- 4) PostgREST 스키마 캐시 갱신
--    (Supabase는 스키마 변경 후 캐시가 즉시 갱신되지 않을 수 있어 명시적으로 재로딩 신호 발송)
NOTIFY pgrst, 'reload schema';

-- ============================================================
-- 검증 쿼리 (적용 후 실행해서 컬럼 구성 확인)
-- ============================================================
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'stock_analysis_results'
-- ORDER BY ordinal_position;

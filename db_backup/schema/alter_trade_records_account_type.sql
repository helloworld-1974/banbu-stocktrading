-- ============================================================
-- trade_records 에 account_type 컬럼 추가
-- ------------------------------------------------------------
-- 모의투자(mock) / 실전투자(real) 거래 기록을 한 테이블에서 구분.
-- KIS_USE_MOCK 변경 시 다른 계좌의 거래가 서로 간섭하지 않도록 분리.
--
-- 동작:
--   * 기존 모든 레코드는 모의투자 시절이므로 'mock' 으로 backfill
--   * 신규 INSERT 는 settings.KIS_USE_MOCK 에 따라 'mock' 또는 'real'
--   * 코드의 모든 SELECT/UPDATE 는 account_type 으로 추가 필터
--
-- 사용:
--   Supabase SQL Editor 에서 한 번 실행
-- ============================================================

-- 1) account_type 컬럼 추가 (DEFAULT 'mock' 으로 기존 레코드 자동 backfill)
ALTER TABLE trade_records
    ADD COLUMN IF NOT EXISTS account_type TEXT NOT NULL DEFAULT 'mock';

-- 2) 혹시 NULL 이 있으면 명시적 backfill (DEFAULT 가 안 먹은 경우 대비)
UPDATE trade_records SET account_type = 'mock' WHERE account_type IS NULL;

-- 3) account_type 별 인덱스 (자주 필터링되는 컬럼)
CREATE INDEX IF NOT EXISTS idx_trade_records_account_type
    ON trade_records (account_type);

-- 4) account_type + status 복합 인덱스 (reconcile/auto_buy 가 자주 사용)
CREATE INDEX IF NOT EXISTS idx_trade_records_account_status
    ON trade_records (account_type, status);

-- ============================================================
-- 검증 쿼리
-- ============================================================
-- SELECT account_type, status, COUNT(*)
-- FROM trade_records
-- GROUP BY account_type, status
-- ORDER BY account_type, status;
--
-- 기대 결과:
--   account_type | status   | count
--   mock         | holding  | 6
--   mock         | sold     | 4

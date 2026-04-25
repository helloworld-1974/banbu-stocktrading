-- 1) RLS 비활성화
ALTER TABLE IF EXISTS economic_and_stock_data DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS stock_analysis_results DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS stock_recommendations DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS ticker_sentiment_analysis DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS access_tokens DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS stocks DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS predicted_stocks DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS trade_records DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS llm_decision_logs DISABLE ROW LEVEL SECURITY;

-- 2) public 스키마의 모든 테이블에 대해 id 시퀀스를 max(id) + 1 로 재동기화
-- 예를 들어 economic_and_stock_data 테이블의 마지막 행이 id=2865라면 시퀀스를 2866으로 reset 
-- → 다음 INSERT 시 충돌 안 남.
DO $$
DECLARE
    r RECORD;
    seq_name TEXT;
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        seq_name := pg_get_serial_sequence('public.' || quote_ident(r.tablename), 'id');
        IF seq_name IS NOT NULL THEN
            EXECUTE format(
                'SELECT setval(%L, COALESCE((SELECT MAX(id) FROM public.%I), 0) + 1, false)',
                seq_name, r.tablename
            );
        END IF;
    END LOOP;
END $$;
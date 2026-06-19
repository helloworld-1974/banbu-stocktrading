# KIS 한국주식 매수/매도 수동 테스트 스크립트

`app_kor` 의 `balance_service` 주문/잔고/시세 API 를 실제로 호출해 배선과 체결을 검증하는 수동 테스트 도구.
모두 **프로젝트 루트에서** 실행한다 (`from app_kor...` import 경로 때문).

## test_kor_trade.py — 모의투자(MOCK) 전용

토큰 → 잔고 → 현재가 → 매수가능금액 → 시장가 매수 → 시장가 매도 → 잔고 를 순차 호출.
`KIS_USE_MOCK=true` 일 때만 동작(실거래면 즉시 중단). KRX 정규장(09:00~15:30 KST)에 체결됨.

```bash
venv/bin/python scripts/test_kor_trade.py [종목코드] [수량]
# 예) venv/bin/python scripts/test_kor_trade.py 005930 1   # 삼성전자 1주
```

## test_kor_nxt_real.py — NXT 애프터마켓 ★실거래★ 왕복

실제 계좌·실제 돈으로 NXT(넥스트레이드)에서 1주 매수 → 1주 매도(round-trip).
호가 기준 지정가(매도1호가에 매수 / 매수1호가에 매도)로 즉시 체결을 노린다.
`KIS_USE_MOCK=false` 전용(모의면 즉시 중단).

```bash
KIS_USE_MOCK=false venv/bin/python scripts/test_kor_nxt_real.py [종목코드]
# 기본 종목: 011200 (HMM)
```

### 주의사항
- **실제 돈이 나간다.** 스프레드 + 수수료/거래세(왕복 약 100원 내외) 비용 발생.
- NXT 애프터마켓 운영시간 **15:30~20:00 KST** 에만 체결. 이 시간 외에는 호가가 없어 매수 미체결.
- NXT 호가가 살아있는 종목만 가능. 저가주는 NXT 호가가 `0` 일 수 있음(이 경우 KRX 정규장 이용).
- 거래소 지정 주문은 신 TR_ID(`TTTC0012U`/`TTTC0011U`) + `EXCG_ID_DVSN_CD`(KRX/NXT/SOR) 사용.
- 주문가능현금이 매수단가 이상 있어야 함. 5만원 이내 종목 권장.
```

"""
한국투자증권(KIS) 국내주식 잔고/주문/시세 서비스.

미국판(app/services/balance_service.py) 의 해외주식 로직을 국내주식 TR 로 치환:
  - 잔고:        /uapi/domestic-stock/v1/trading/inquire-balance   (TTTC8434R / VTTC8434R)
  - 현금주문:    /uapi/domestic-stock/v1/trading/order-cash         (매수 TTTC0802U / 매도 TTTC0801U)
  - 매수가능:    /uapi/domestic-stock/v1/trading/inquire-psbl-order (TTTC8908R / VTTC8908R)
  - 현재가:      /uapi/domestic-stock/v1/quotations/inquire-price   (FHKST01010100)
  - 정정취소가능(미체결): /uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl (TTTC8036R)

토큰은 계좌 단위(국내·해외 공용)이므로 access_tokens 테이블을 그대로 공유한다.
"""
import requests
import time
from datetime import datetime, timedelta
import pytz
from threading import Lock

from app_kor.core.config import settings
from app_kor.core.constants import KIS_MARKET_DIV_CODE
from app_kor.db.supabase import supabase
from app_kor.services.auth_service import parse_expiration_date

# 메모리 토큰 캐시 — mock/real 별도 슬롯
_token_cache = {
    "kis_mock": {"access_token": None, "expires_at": None},
    "kis_real": {"access_token": None, "expires_at": None},
}
_last_refresh_time = {"kis_mock": 0, "kis_real": 0}
_refresh_lock = Lock()


def _current_token_type() -> str:
    """현재 활성 모드의 토큰 타입 반환 (kis_mock 또는 kis_real)"""
    return "kis_mock" if settings.KIS_USE_MOCK else "kis_real"


def current_account_type() -> str:
    """trade_records_kor.account_type 컬럼용 — 'mock' or 'real'."""
    return "mock" if settings.KIS_USE_MOCK else "real"


def get_access_token():
    """KIS API 접근 토큰 발급 또는 캐시된 토큰 반환.
    KIS_USE_MOCK 에 따라 mock/real 토큰을 분리해서 캐시/저장 (국내·해외 공용)."""
    global _token_cache, _last_refresh_time

    token_type = _current_token_type()
    cache = _token_cache[token_type]
    now = datetime.now(pytz.UTC)

    if cache["access_token"] and cache["expires_at"] and now < cache["expires_at"]:
        return cache["access_token"]

    current_time = time.time()
    last_refresh = _last_refresh_time[token_type]
    if current_time - last_refresh < 60:
        time_to_wait = 60 - (current_time - last_refresh)
        print(f"1분 제한으로 {time_to_wait:.1f}초 대기 ({token_type})")
        time.sleep(time_to_wait)

    with _refresh_lock:
        if cache["access_token"] and cache["expires_at"] and now < cache["expires_at"]:
            return cache["access_token"]

        try:
            response = supabase.table("access_tokens") \
                .select("*") \
                .eq("token_type", token_type) \
                .order("updated_at", desc=True) \
                .limit(1).execute()

            if response.data:
                token_data = response.data[0]
                expiration_time = parse_expiration_date(token_data["expires_at"])

                if now < expiration_time:
                    print(f"DB 기존 토큰 사용 ({token_type}) - 만료까지: {(expiration_time - now)}")
                    cache["access_token"] = token_data["access_token"]
                    cache["expires_at"] = expiration_time
                    _last_refresh_time[token_type] = current_time
                    return token_data["access_token"]

                print(f"토큰 만료됨, 갱신 필요 ({token_type})")
                token = refresh_token_with_retry(token_type=token_type, record_id=token_data["id"])
            else:
                print(f"토큰 레코드 없음 ({token_type}), 새로 생성")
                token = refresh_token_with_retry(token_type=token_type)

            cache["access_token"] = token
            cache["expires_at"] = now + timedelta(days=1)
            _last_refresh_time[token_type] = current_time
            return token

        except Exception as e:
            print(f"토큰 조회 오류 ({token_type}): {str(e)}")
            if cache["access_token"]:
                return cache["access_token"]
            raise Exception(f"토큰 발급 실패 ({token_type}): {str(e)}")


def refresh_token_with_retry(token_type: str = None, record_id=None, max_retries=3):
    """토큰 갱신을 재시도하며 처리."""
    if token_type is None:
        token_type = _current_token_type()

    for attempt in range(max_retries):
        try:
            url = f"{settings.kis_base_url}/oauth2/tokenP"
            data = {
                "grant_type": "client_credentials",
                "appkey": settings.KIS_APPKEY,
                "appsecret": settings.KIS_APPSECRET,
            }
            response = requests.post(url, json=data)
            response_data = response.json()

            if 'access_token' not in response_data:
                raise Exception(f"토큰 발급 실패: {response_data}")

            access_token = response_data["access_token"]
            expires_in = response_data.get("expires_in", 86400)
            now = datetime.now(pytz.UTC)
            expiration_time = now + timedelta(seconds=expires_in)

            token_data = {
                "token_type": token_type,
                "access_token": access_token,
                "expires_at": expiration_time.isoformat(),
            }

            if record_id:
                supabase.table("access_tokens").update(token_data).eq("id", record_id).execute()
                print(f"토큰 업데이트 완료 ({token_type})")
            else:
                supabase.table("access_tokens").insert(token_data).execute()
                print(f"새 토큰 레코드 생성 완료 ({token_type})")

            return access_token

        except Exception as e:
            print(f"토큰 갱신 오류 ({token_type}, 시도 {attempt+1}/{max_retries}): {str(e)}")
            if "EGW00133" in str(e) and attempt < max_retries - 1:
                print("1분 제한 에러, 61초 대기 후 재시도")
                time.sleep(61)
            else:
                raise


def _headers(tr_id: str) -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": settings.KIS_APPKEY,
        "appsecret": settings.KIS_APPSECRET,
        "tr_id": tr_id,
    }


# ══════════════════════════════════════════════════════════════════
# 잔고 조회
# ══════════════════════════════════════════════════════════════════

def get_domestic_balance():
    """국내주식 잔고 조회.

    Returns: KIS 원본 응답
        output1: 보유 종목 리스트
            pdno(종목코드), prdt_name(종목명), hldg_qty(보유수량),
            ord_psbl_qty(주문가능수량), pchs_avg_pric(매입평균가), prpr(현재가),
            evlu_amt(평가금액), pchs_amt(매입금액), evlu_pfls_amt(평가손익),
            evlu_pfls_rt(평가손익율)
        output2: 계좌 요약 [{dnca_tot_amt(예수금), tot_evlu_amt(총평가), nass_amt(순자산), ...}]
    """
    access_token = get_access_token()
    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "VTTC8434R" if settings.KIS_USE_MOCK else "TTTC8434R"

    headers = _headers(tr_id)
    params = {
        "CANO": settings.KIS_CANO,
        "ACNT_PRDT_CD": settings.KIS_ACNT_PRDT_CD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params)
            result = response.json()

            if 'rt_cd' in result and result['rt_cd'] != '0' and attempt < max_retries - 1:
                msg1 = result.get('msg1', '알 수 없는 오류')
                print(f"API 오류: {result.get('msg_cd', 'N/A')} - {msg1}. 재시도...")
                if "초당" in msg1:
                    time.sleep(2)
                else:
                    headers["authorization"] = f"Bearer {get_access_token()}"
                    time.sleep(1)
                continue

            return result
        except Exception as e:
            print(f"잔고 조회 중 오류 발생 (시도 {attempt+1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise


def get_all_balances():
    """전체 보유 잔고 조회 (국내주식은 단일 호출).

    미국판 get_all_overseas_balances() 와 동일한 시그니처/반환 형태를 제공해
    scheduler/recommendation 코드가 동일한 인터페이스로 사용 가능하게 함.
    """
    result = get_domestic_balance()
    if not result or result.get("rt_cd") != "0":
        return {
            "rt_cd": result.get("rt_cd", "1") if result else "1",
            "msg_cd": result.get("msg_cd", "ERROR") if result else "ERROR",
            "msg1": result.get("msg1", "잔고 조회 실패") if result else "잔고 조회 실패",
            "output1": [],
            "output2": [],
        }
    # 보유수량 0 종목 제외
    output1 = [h for h in result.get("output1", []) if int(h.get("hldg_qty", 0) or 0) > 0]
    return {
        "rt_cd": "0",
        "msg_cd": result.get("msg_cd", "00000"),
        "msg1": result.get("msg1", "정상 처리"),
        "output1": output1,
        "output2": result.get("output2", []),
    }


# ══════════════════════════════════════════════════════════════════
# 현재가 조회
# ══════════════════════════════════════════════════════════════════

def get_current_price(ticker: str, market_div_code: str = None):
    """국내주식 현재가 조회 (inquire-price, FHKST01010100).

    Args:
        ticker: 6자리 종목코드 (예: "005930")
        market_div_code: 시장분류코드. 기본 KIS_MARKET_DIV_CODE("J", KRX).
            NXT 단독 시세는 "NX", KRX+NXT 통합 시세는 "UN".

    Returns: KIS 원본 응답. output.stck_prpr 가 현재가(원, 문자열).
    """
    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = _headers("FHKST01010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": market_div_code or KIS_MARKET_DIV_CODE,
        "FID_INPUT_ISCD": ticker,
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        return response.json()
    except Exception as e:
        print(f"현재가 조회 중 오류 발생 ({ticker}): {str(e)}")
        raise


# ══════════════════════════════════════════════════════════════════
# 매수가능금액 조회
# ══════════════════════════════════════════════════════════════════

def inquire_psbl_amount(ticker: str, ord_unpr: str, ord_dvsn: str = "00"):
    """국내주식 매수가능금액 조회 (inquire-psbl-order, TTTC8908R).

    Returns: KIS 원본 응답.
        output.ord_psbl_cash (주문가능현금),
        output.nrcvb_buy_amt (미수없는매수금액),
        output.max_buy_amt (최대매수금액),
        output.max_buy_qty (최대매수수량)
    """
    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    tr_id = "VTTC8908R" if settings.KIS_USE_MOCK else "TTTC8908R"
    headers = _headers(tr_id)
    params = {
        "CANO": settings.KIS_CANO,
        "ACNT_PRDT_CD": settings.KIS_ACNT_PRDT_CD,
        "PDNO": ticker,
        "ORD_UNPR": str(ord_unpr),
        "ORD_DVSN": ord_dvsn,
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        return response.json()
    except Exception as e:
        print(f"매수가능금액 조회 중 오류 발생 ({ticker}): {str(e)}")
        raise


# ══════════════════════════════════════════════════════════════════
# 주문 (현금 매수/매도)
# ══════════════════════════════════════════════════════════════════

def order_domestic_stock(order_data: dict):
    """국내주식 현금 매수/매도 주문 (order-cash).

    Args:
        order_data: {
            "PDNO": 종목코드,
            "ORD_QTY": 주문수량(str),
            "ORD_UNPR": 주문단가(str, 시장가면 "0"),
            "ORD_DVSN": 주문구분 ("00":지정가, "01":시장가),
            "is_buy": True/False,
            "EXCG_ID_DVSN_CD": 거래소ID구분코드 (선택). 지정 시 NXT 지원
                신 TR_ID(매수 TTTC0012U / 매도 TTTC0011U)로 전송.
                "KRX": 한국거래소, "NXT": 넥스트레이드, "SOR": 스마트오더라우팅.
                미지정 시 기존 KRX 전용 TR_ID(TTTC0802U/0801U) 사용.
        }

    Returns: KIS 원본 응답.
    """
    try:
        if "CANO" not in order_data or not order_data["CANO"]:
            order_data["CANO"] = settings.KIS_CANO
        if "ACNT_PRDT_CD" not in order_data or not order_data["ACNT_PRDT_CD"]:
            order_data["ACNT_PRDT_CD"] = settings.KIS_ACNT_PRDT_CD

        is_virtual = settings.KIS_USE_MOCK
        is_buy = order_data.get("is_buy", True)
        # 거래소ID구분코드가 있으면 NXT/SOR 지원 신 TR_ID, 없으면 기존 KRX 전용 TR_ID.
        use_nxt_api = bool(order_data.get("EXCG_ID_DVSN_CD"))

        if use_nxt_api:
            if is_buy:
                tr_id = "VTTC0012U" if is_virtual else "TTTC0012U"  # 현금 매수(거래소 지정)
            else:
                tr_id = "VTTC0011U" if is_virtual else "TTTC0011U"  # 현금 매도(거래소 지정)
        else:
            if is_buy:
                tr_id = "VTTC0802U" if is_virtual else "TTTC0802U"  # 현금 매수(KRX)
            else:
                tr_id = "VTTC0801U" if is_virtual else "TTTC0801U"  # 현금 매도(KRX)

        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/trading/order-cash"
        headers = _headers(tr_id)

        request_body = order_data.copy()
        request_body.pop("is_buy", None)
        if "ORD_DVSN" not in request_body:
            request_body["ORD_DVSN"] = "00"  # 지정가

        print(f"국내주식 주문 API 요청: {url} tr_id={tr_id}")
        print(f"요청 본문: {request_body}")

        response = requests.post(url, headers=headers, json=request_body)
        print(f"API 응답 상태 코드: {response.status_code}")
        print(f"API 응답 본문: {response.text[:200] if response.text else '비어있음'}")

        if response.status_code != 200:
            return {
                "rt_cd": "1",
                "msg_cd": f"HTTP_{response.status_code}",
                "msg1": f"API 호출 실패: HTTP {response.status_code}",
                "output": {},
            }
        try:
            return response.json()
        except ValueError:
            return {"rt_cd": "1", "msg_cd": "PARSEERR", "msg1": "응답 파싱 오류", "output": {}}
    except Exception as e:
        print(f"국내주식 주문 중 오류 발생: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return {"rt_cd": "1", "msg_cd": "ERROR", "msg1": f"API 호출 오류: {str(e)}", "output": {}}


# ══════════════════════════════════════════════════════════════════
# 미체결(정정취소가능) 내역 조회
# ══════════════════════════════════════════════════════════════════

def get_domestic_nccs():
    """국내주식 정정취소가능주문(미체결) 조회 (inquire-psbl-rvsecncl, TTTC8036R).

    모의투자는 미지원. 미지원 시 안내 메시지 반환.
    """
    if settings.KIS_USE_MOCK:
        return {
            "rt_cd": "0",
            "msg_cd": "MOCK_UNSUPPORTED",
            "msg1": "모의투자 환경에서는 정정취소가능주문조회를 지원하지 않습니다.",
            "output": [],
        }
    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    headers = _headers("TTTC8036R")
    params = {
        "CANO": settings.KIS_CANO,
        "ACNT_PRDT_CD": settings.KIS_ACNT_PRDT_CD,
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
        "INQR_DVSN_1": "0",
        "INQR_DVSN_2": "0",
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        return response.json()
    except Exception as e:
        print(f"미체결내역 조회 중 오류 발생: {str(e)}")
        return {"rt_cd": "1", "msg_cd": "ERROR", "msg1": f"API 호출 오류: {str(e)}", "output": []}

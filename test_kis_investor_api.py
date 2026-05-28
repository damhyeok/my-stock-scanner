import os
from dataclasses import dataclass
from pprint import pprint

import requests
from dotenv import load_dotenv


BASE_URL = "https://openapi.koreainvestment.com:9443"


@dataclass
class ApiProbe:
    name: str
    path: str
    tr_id: str
    params: dict


def get_access_token(app_key, app_secret):
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    response = requests.post(url, headers=headers, json=body, timeout=10)
    print("Token API status:", response.status_code)
    response.raise_for_status()

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Token API did not return access_token: {data}")
    return token


def request_probe(app_key, app_secret, token, probe):
    url = f"{BASE_URL}{probe.path}"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": probe.tr_id,
        "custtype": "P",
    }
    response = requests.get(url, headers=headers, params=probe.params, timeout=10)
    print(f"{probe.name} status:", response.status_code)
    return response


def summarize_response(data):
    print("rt_cd:", data.get("rt_cd"))
    print("msg_cd:", data.get("msg_cd"))
    print("msg1:", data.get("msg1"))

    for key in ("output", "output1", "output2"):
        value = data.get(key)
        if isinstance(value, list):
            print(f"{key}: list rows={len(value)}")
            if value:
                print(f"{key} first row fields:")
                pprint(sorted(value[0].keys()))
                print(f"{key} first row sample:")
                pprint(value[0])
        elif isinstance(value, dict):
            print(f"{key}: dict fields:")
            pprint(sorted(value.keys()))
            print(f"{key} sample:")
            pprint(value)
        elif value is not None:
            print(f"{key}: {type(value).__name__}")


def summarize_http_response(response):
    if response.status_code != 200:
        print("HTTP error body:")
        print(response.text[:500])
        return

    try:
        data = response.json()
    except ValueError:
        print("Non-JSON response:")
        print(response.text[:500])
        return

    summarize_response(data)


def get_probe_cases():
    return [
        ApiProbe(
            name="old-investor-trend-candidate",
            path="/uapi/domestic-stock/v1/quotations/investor-trend",
            tr_id="FHPST02310000",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20168",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        ),
        ApiProbe(
            name="foreign-institution-total-all-net-buy-amount",
            path="/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            tr_id="FHPTJ04400000",
            params={
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": "0",
            },
        ),
        ApiProbe(
            name="foreign-institution-total-foreign-net-buy-amount",
            path="/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            tr_id="FHPTJ04400000",
            params={
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": "1",
            },
        ),
        ApiProbe(
            name="foreign-institution-total-institution-net-buy-amount",
            path="/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            tr_id="FHPTJ04400000",
            params={
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": "2",
            },
        ),
        ApiProbe(
            name="inquire-investor-single-stock",
            path="/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": "005930",
            },
        ),
        ApiProbe(
            name="investor-trend-estimate-single-stock",
            path="/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
            tr_id="HHPTJ04160200",
            params={
                "MKSC_SHRN_ISCD": "005930",
            },
        ),
    ]


def main():
    load_dotenv(".env")
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()

    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET must be set.")

    token = get_access_token(app_key, app_secret)
    for probe in get_probe_cases():
        print("\n" + "=" * 80)
        print("Probe:", probe.name)
        print("Path:", probe.path)
        print("TR ID:", probe.tr_id)
        print("Params:")
        pprint(probe.params)
        response = request_probe(app_key, app_secret, token, probe)
        summarize_http_response(response)


if __name__ == "__main__":
    main()

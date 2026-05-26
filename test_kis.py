import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
appkey = os.environ.get('KIS_APP_KEY', '').strip()
appsecret = os.environ.get('KIS_APP_SECRET', '').strip()

print(f"Key length: {len(appkey)}")

url_token = 'https://openapi.koreainvestment.com:9443/oauth2/tokenP'
res = requests.post(url_token, json={'grant_type': 'client_credentials', 'appkey': appkey, 'appsecret': appsecret})
token = res.json().get('access_token')

if not token:
    print("Token failed:", res.text)
else:
    print("Token success")

    url_vol = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/volume-rank'
    headers = {
        'content-type': 'application/json; charset=utf-8',
        'authorization': f'Bearer {token}',
        'appkey': appkey,
        'appsecret': appsecret,
        'tr_id': 'FHPST01710000',
        'custtype': 'P'
    }
    params = {
        'FID_COND_MRKT_DIV_CODE': 'J',
        'FID_COND_SCR_DIV_CODE': '20171',
        'FID_INPUT_ISCD': '0000',
        'FID_DIV_CLS_CODE': '0',
        'FID_BLNG_CLS_CODE': '0',
        'FID_TRGT_CLS_CODE': '111111111',
        'FID_TRGT_EXLS_CLS_CODE': '000000',
        'FID_INPUT_PRICE_1': '',
        'FID_INPUT_PRICE_2': '',
        'FID_VOL_CNT': '',
        'FID_INPUT_DATE_1': ''
    }
    res_vol = requests.get(url_vol, headers=headers, params=params)
    print("Volume API:", res_vol.status_code, res_vol.text[:300])


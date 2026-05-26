import requests
from bs4 import BeautifulSoup
import pandas as pd

def get_naver_investor_top(mac=1):
    url = f"https://finance.naver.com/sise/sise_deal_rank.naver?mac={mac}"
    res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(res.text, 'html.parser')
    
    # <tr> 찾기
    rows = soup.select('table.type_2 tbody tr')
    tickers = []
    
    for row in rows:
        a_tag = row.select_one('a.tltle')
        if a_tag:
            href = a_tag.get('href', '')
            if 'code=' in href:
                ticker = href.split('code=')[-1]
                tickers.append(ticker)
                
    return tickers

print("Foreign Top:", get_naver_investor_top(1)[:5])
print("Inst Top:", get_naver_investor_top(2)[:5])

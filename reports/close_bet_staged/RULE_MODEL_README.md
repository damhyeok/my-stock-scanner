# 단계형 종가베팅 규칙 모델

## 모델 구조

최종 기술조건은 점수를 합산하지 않고 다음 단계를 모두 통과해야 한다.

```text
유동성
AND Price Action (A OR B OR C)
AND AVWAP·위치
AND 오후 흐름
AND 상대강도
AND 변동성
```

시장강도는 모델 밖의 수동 게이트다. 기술조건을 통과해도 시장 판단이 입력되지 않으면 `기술조건 통과·시장판단 대기`로 저장한다.

## Price Action

- A: 확인된 이전 저점보다 최근 저점이 높고, 지지선 부근을 유지하며 높은 CLV로 마감
- B: 5·10·20·60일 중 하나의 직전 고점을 종가로 돌파하고 윗꼬리가 과도하지 않음
- C: 장중 확인 지지선을 이탈했다가 종가에 회복하고 아랫꼬리와 거래량이 증가

A/B/C 중 하나만 만족하면 이 단계를 통과한다.

## 데이터 품질

- AVWAP: 일봉 근사치. 최근 확정 스윙저점과 최근 20일 거래량 집중일을 앵커로 사용
- POC: 저장된 선택 시점 분봉에서 거래량이 가장 많은 봉의 종가를 사용한 표본 프록시
- 오후 흐름: 14:30~15:30 저장 봉 기준 수익률, 저점 갱신, 상승/하락봉 거래량, CLV 중 3개 이상
- 업종 RS: 동일 날짜 업종 구성 종목의 5일 수익률 중앙값 대비 프록시
- 시장 RS: 분석 유니버스 20일 수익률 중앙값 대비 프록시
- 변동성: ATR(14)%의 당일 횡단면 분위수

정확한 업종지수, 전체 분봉 Volume Profile, 체결 Delta로 표현하지 않는다.

## 설정

임계값은 `close_bet_staged/configs/rule_model.json`에서 변경할 수 있다. 기본값은 전략 의미를 구현하기 위한 시작값이며 백테스트 최적화값이 아니다.

## 실행

시장 판단 대기 상태로 분석:

```powershell
python -m close_bet_staged.rule_model_runner --db stock_data.db
```

시장 통과를 수동 적용:

```powershell
python -m close_bet_staged.rule_model_runner --db stock_data.db --market-pass --persist
```

시장 차단을 적용:

```powershell
python -m close_bet_staged.rule_model_runner --db stock_data.db --market-block --persist
```

전체 분석인 `main.py`에서는 시장 대기 상태로 평가 결과를 저장한다. 대시보드의 `종가베팅 스캐너` 탭에서 기술 통과 후보를 확인하고 `시장강도` 탭을 보고 사용자가 최종 결정한다.

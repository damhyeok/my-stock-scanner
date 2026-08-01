# Phase 2 공통 엔진 구현 현황

작성일: 2026-08-01
상태: 기본 구현 완료, 실시간 데이터 연결 전

## 구현된 항목

- `market_betting_engine/contracts.py`
  - `Observation`, `ObservationMeta`, `MetricResult`, `DataQualityReport`, `Judgment` 분리
  - Actual/Proxy/Activity/Derived 계산 모드 구분
  - 평가 재현용 `EvaluationTrace` 계약 정의
  - 시장·섹터·종목·종가 신규진입·기존보유 상태 정의
- `market_betting_engine/quality.py`
  - 관측 시각 지연, 원천 거래일 불일치, 교차 소스 거래일 충돌 검사
  - 필수 지표 누락과 필드 의미 미검증 상태 검사
  - 데이터 부족을 약세로 해석하지 않고 판단 불가 상태로 전달
- `market_betting_engine/metrics.py`
  - CLV, VWAP, 상대수익률, OLS 기울기, 활동 속도 변화
  - CLV 가중 거래대금 Proxy
  - Flat bar와 거래량 0을 중립값 0으로 위장하지 않고 계산 불가 처리
- `market_betting_engine/states.py`
  - 시장 `ALLOW/SELECTIVE/BLOCK/NOT_EVALUABLE` 게이트
  - 서로 독립된 2개 위험 축이 있어야 Hard Veto가 되는 기본 규칙
  - 종목 `WATCH/SETUP/TRIGGERED/EXTENDED/FAILED/INVALIDATED` 전환
  - 구조적 무효화 가격이 없으면 `TRIGGERED` 불허
- `market_betting_engine/engines.py`
  - 섹터 구성 종목·거래대금 커버리지와 1위 종목 집중도 검사
  - `EMERGING`과 `LEADING` 분리
  - `CLOSE_NEW_ENTRY`와 `HOLD_EXISTING` 판단 분리
  - 임계값 설정에 `placeholder=True` 명시
- `market_betting_engine/session.py`
  - 거래일을 로컬 평일 계산으로 추측하지 않고 외부 거래소 캘린더 판정을 입력받음
  - 마감 구간을 14:30~15:00, 15:00~15:20, 15:20~15:30으로 분리
- `market_betting_engine/adapters.py`
  - KIS·키움 REST 응답을 공통 `Observation`으로 정규화
  - 목표 거래일 외 행과 KIS 지수 특수 시각 행 분리
  - 키움 가격 부호 접두사를 절대 가격으로 정규화
  - 응답에 거래일이 없을 때 `REQUEST_CONTEXT/UNKNOWN` 출처 구분
- `market_betting_engine/collector.py`
  - 기존 Read-Only allow-list를 통과한 응답만 메모리에서 어댑터로 전달
  - 원시 응답을 별도 파일에 저장하지 않고 기존 마스킹 보고서 정책 유지
- `market_betting_engine/orchestrator.py`
  - 데이터 품질→시장→섹터→종목 순서로 판단 실행
  - 실제 판단에서는 거래일과 필드 의미가 모두 확인된 입력만 허용
  - `PARTIAL` API 필드가 자동으로 `ALLOW`에 승격되는 경로 차단

## 검증 결과

실행 명령:

```powershell
.backtest-venv\Scripts\python.exe -m unittest tests.test_api_probe tests.test_market_betting_core -v
```

결과: 총 56개 테스트 통과.

검증된 주요 경계 조건:

- Flat bar CLV
- VWAP 거래량 0
- Proxy와 실제 수급의 타입 분리
- 과거 거래일 데이터와 최신 데이터의 혼합 차단
- 단일 위험 신호와 복수 독립 위험 축 구분
- `SETUP`의 성급한 `TRIGGERED` 전환 차단
- `INVALIDATED` 상태의 명시적 재무장 전까지 유지
- 섹터 단일 종목 독주 착시
- 종가 신규진입과 기존 보유의 서로 다른 결과

## 다음 연결 작업

1. 실제 거래소 세션 캘린더 공급원 연결
2. 정규화된 1분봉으로 VWAP·CLV·활동 속도 시계열 자동 생성
3. 파생값에서 시장·섹터·종목 `AxisSignal`을 만드는 규칙 모듈
4. 오케스트레이터 결과와 근거의 전용 SQLite 저장
5. 장중에 WebSocket 원문을 확보한 뒤 체결 방향 `UNKNOWN` 버퍼 연결

WebSocket 체결·호가의 실제 필드 의미와 실시간 지연 특성은 장중 검증 전까지 확정하지 않는다.

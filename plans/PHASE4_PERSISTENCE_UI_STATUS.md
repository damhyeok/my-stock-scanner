# 판단 저장 및 기존 Streamlit 탭 통합 현황

작성일: 2026-08-01

## 구현 내용

### SQLite 저장

`market_betting_engine/storage.py`가 기존 `stock_data.db` 안에 다음 전용 테이블을 생성할 수 있다.

- `market_betting_runs`: 실행 시각, 대상 거래일, 세션 구간, 엔진·설정 버전, 시장 판단, 데이터 품질
- `market_betting_judgments`: 시장·섹터·오버나이트 판단과 지지·반대·경고·차단 근거
- `market_betting_stock_states`: 종목별 이전/현재 상태와 전환 사유
- `market_betting_observations`: 판단에 사용한 관측값과 원천·단위·검증 상태·거래일 출처

한 번의 판단 실행은 단일 SQLite 트랜잭션으로 저장된다. 동일 `run_id` 중복이나 저장 오류가 발생하면 일부 테이블만 남지 않는다.

### 기존 Streamlit 통합

기존 `app.py`의 14번째 탭으로 다음 항목을 추가했다.

```text
🧠 장중·오버나이트 분석
```

표시 항목:

- 장중 시장 진입 허용 상태
- 종가 신규진입과 기존 보유의 분리 결과
- 데이터 품질 차단 여부와 관측값 수
- 시장 판단의 지지 근거·반대 증거·경고·차단 사유
- 섹터별 `LEADING/EMERGING/NEUTRAL/FADING/AVOID`
- 종목별 `WATCH/SETUP/TRIGGERED/EXTENDED/FAILED/INVALIDATED`
- 설정 버전·엔진 버전·세션 구간
- 저장된 파생값 원문

선택 날짜에 결과가 없을 경우 다른 거래일 결과로 자동 대체하지 않는다.

## 기존 코드 보호

`app.py`에서는 다음 연결부만 추가했다.

1. 탭 렌더러 import
2. 이미 캐시되는 DB 경로 재사용
3. 기존 탭 목록에 새 탭 추가
4. 기존 마지막 섹터 탭의 `st.stop()`보다 앞에서 새 탭 렌더링

기존 13개 탭의 내부 계산과 사용자 수정 내용은 변경하지 않았다.

## 검증

- 전체 관련 단위·통합 테스트: 76개 통과
- `app.py` 및 신규 패키지 Python 문법 검사 통과
- 빈 DB·테이블 미생성 상태에서 새 탭 조회 안전 처리
- 날짜 필터가 다른 거래일로 대체되지 않는 것 확인
- 장중·섹터·종목·오버나이트 결과 저장 및 재조회 확인
- 중복 실행 ID 트랜잭션 롤백 확인

로컬 테스트 Python 환경에는 Streamlit 패키지가 설치되어 있지 않아 실제 브라우저 렌더링 검증은 수행하지 않았다. 배포 환경의 `requirements.txt`에는 `streamlit`이 포함되어 있다.

## 다음 연결

현재 탭은 저장된 판단을 읽는 기능까지 완성되어 있다. 다음 작업은 Oracle/GitHub Actions의 정기 분석 과정에서 다음 순서를 실행하도록 연결하는 것이다.

```text
Read-Only 수집
→ Observation 정규화
→ 파생값·AxisSignal 생성
→ 시장·섹터·종목·오버나이트 판단
→ save_decision_cycle(stock_data.db)
→ 기존 Streamlit 새 탭 표시
```

실제 API 필드가 `PARTIAL`인 동안에는 실행 기록을 저장하더라도 판단은 `NOT_EVALUABLE`로 남기는 것이 기본 안전 정책이다.

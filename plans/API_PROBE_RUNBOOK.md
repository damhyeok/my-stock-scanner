# Phase 1 Read-Only API 검증 운영 가이드

작성 기준일: 2026-08-01 (KST)

## 목적

`market_betting_engine.api_probe`는 장중·오버나이트 판단 엔진을 만들기 전에 KIS와 키움 시세 API의 실제 응답 필드, 단위, 가용 시각을 확인한다.

- 주문·계좌·잔고 API는 호출하지 않는다.
- 명시적인 시세 조회 allow-list 밖의 경로는 코드에서 거부한다.
- 인증 헤더와 토큰은 저장하지 않는다.
- 응답 샘플은 민감정보 제거와 행 수 제한 후 저장한다.
- 성공 응답도 단위와 의미를 사람이 확인하기 전에는 자동으로 `VERIFIED`가 되지 않는다.

## 명령

등록된 검증 항목만 확인:

```powershell
.backtest-venv\Scripts\python.exe -m market_betting_engine.api_probe --list
```

KIS 삼성전자 현재가 한 건 검증:

```powershell
.backtest-venv\Scripts\python.exe -m market_betting_engine.api_probe --probe kis_stock_price --ticker 005930
```

KIS 실행 가능한 REST 검증 전체 실행:

```powershell
.backtest-venv\Scripts\python.exe -m market_betting_engine.api_probe --all-executable --provider KIS
```

키움 종목 분봉 검증:

```powershell
.backtest-venv\Scripts\python.exe -m market_betting_engine.api_probe --probe kiwoom_stock_minute --ticker 005930
```

## 결과

- SQLite: `reports/api_probes/api_probe_results.db`
- 민감정보 제거 JSON: `reports/api_probes/probe_report_YYYYMMDD_HHMMSS.json`

SQLite의 `api_probe_runs`에는 실행 상태, 관측 필드, 누락된 예상 필드, 구조 스키마, 제한된 응답 샘플이 저장된다.

## 상태 의미

| 상태 | 의미 |
|---|---|
| `SUCCESS / PARTIAL` | 호출과 예상 필드는 확인됐지만 의미·단위 검토 전 |
| `SCHEMA_MISMATCH / PARTIAL` | 응답은 성공했지만 예상 필드가 일부 없음 |
| `EMPTY_OUTPUT / PARTIAL` | 성공 응답이나 현재 조회 결과가 비어 있음 |
| `PROVIDER_ERROR / UNVERIFIED` | 증권사가 오류 코드를 반환 |
| `ERROR / UNVERIFIED` | 인증·네트워크·파싱 등 실행 오류 |
| `SKIPPED / PENDING_MARKET_SESSION` | 장중에만 검증할 수 있어 대기 |

## 장중에만 가능한 후속 검증

- KIS `H0UPPGM0`: 프로그램 실시간 필드와 단위
- KIS `H0STCNT0`: `CCLD_DVSN` 값 의미와 체결 시각
- KIS `H0STASP0`: 호가 패킷 시각과 체결 스트림 순서
- 키움 `ka90005`: 현재 거래일과 마감 시점의 프로그램·베이시스

토요일·휴장일에는 이 항목을 강제로 확정하지 않는다.

## 검수 절차

1. `execution_status`가 성공인지 확인한다.
2. `observed_fields`와 공식 문서 필드를 대조한다.
3. 샘플 값과 HTS 화면을 같은 시각에 비교한다.
4. 금액·수량·포인트 단위를 확정한다.
5. 당일 데이터가 실제로 제공되는 시각과 지연을 기록한다.
6. 확인 결과를 개발 기획서의 API 상태표에 반영한다.
7. 모든 조건을 충족한 항목만 수동 검토 후 `VERIFIED`로 승격한다.

## 수동 승인 등록부

승인 결과는 `config/market_betting_field_verification.json`에 기록한다. 탐침의
거래일·시각 계약이 먼저 `VERIFIED`여야 하며, 개별 필드에도 단위, 의미,
검토 시각과 근거 보고서 경로가 있어야 한다. 기본 상태는 항상 `PARTIAL`이며
성공 응답만으로 자동 승인되지 않는다.

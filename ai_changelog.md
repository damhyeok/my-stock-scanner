## [2026-05-29 00:57] KIS 상장주식수 기반 시가총액 계산 추가
- **작업 목적:** 시장 데이터 저장 시 `market_cap`이 항상 0으로 저장되던 문제를 해결했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - KIS 거래량 순위 API 응답의 `lstn_stcn` 값을 `listed_shares`로 매핑했습니다.
  - `close * listed_shares` 방식으로 `market_cap`을 계산하도록 변경했습니다.
  - 수급 상위 종목의 개별 시세 보강 조회에서도 `lstn_stcn`이 응답될 경우 시가총액을 함께 채우도록 했습니다.
---

## [2026-05-29 00:53] 수급 병합 종목명 보존 및 결측 처리 개선
- **작업 목적:** 시장 데이터와 KIS 수급 데이터를 병합할 때 종목명이 `name_x/name_y`로 분리된 뒤 저장 시 빈 값으로 남는 문제를 해결했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - 시장 데이터명과 수급 데이터명을 병합 직후 하나의 `name` 컬럼으로 통합하도록 추가했습니다.
  - 전체 DataFrame 결측 처리에서 문자 컬럼(`ticker`, `name`, `sector`, `theme`)과 숫자 컬럼을 분리해, 종목명이 0으로 덮이지 않도록 변경했습니다.
  - 수급 상위 종목처럼 시장 데이터에 없는 종목도 KIS 수급 API의 종목명을 유지하도록 했습니다.
---

## [2026-05-29 00:38] Actions rebase 자동 stash 적용
- **작업 목적:** 자동 커밋 후 남아 있는 unstaged 변경 때문에 `git pull --rebase`가 실패하던 문제를 해결했습니다.
- **영향을 받은 파일:** `.github/workflows/main.yml`, `ai_changelog.md`
- **주요 변경 사항:**
  - GitHub Actions의 DB/리포트 저장 step에서 `git pull --rebase origin main`을 `git pull --rebase --autostash origin main`으로 변경했습니다.
  - rebase 전 남아 있는 작업 트리 변경을 자동으로 임시 보관했다가 다시 적용할 수 있게 했습니다.
---

## [2026-05-29 00:21] GitHub Actions 자동 커밋 푸시 충돌 완화
- **작업 목적:** 자동 실행이 DB/리포트 커밋을 만든 뒤 원격 `main`에 새 커밋이 있어 `git push`가 거절되는 문제를 줄였습니다.
- **영향을 받은 파일:** `.github/workflows/main.yml`, `ai_changelog.md`
- **주요 변경 사항:**
  - GitHub Actions의 DB/리포트 저장 step을 `if` 블록으로 정리해 변경사항이 있을 때만 커밋/푸시하도록 명확히 했습니다.
  - 자동 커밋 생성 후 `git pull --rebase origin main`을 수행한 뒤 `git push`하도록 변경해 원격 브랜치 선행 커밋을 반영하게 했습니다.
---

## [2026-05-29 00:14] 수급 데이터 수집 KIS API 전환
- **작업 목적:** 네이버 금융 크롤링에 의존하던 외국인/기관 수급 데이터 수집을 검증된 KIS `foreign-institution-total` API 기반으로 전환했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `get_investor_data()`가 네이버 HTML 크롤링 대신 KIS `foreign-institution-total` API를 호출하도록 변경했습니다.
  - 외국인 순매수 상위와 기관 순매수 상위를 각각 조회해 `foreign_net`, `inst_net`, `ticker`, `name`으로 병합하도록 매핑했습니다.
  - KIS 수급 조회 실패 시 기존처럼 경고를 남기고 빈 수급 DataFrame을 반환해 자동화가 중단되지 않도록 유지했습니다.
---

## [2026-05-29 00:07] KIS 수급 API 후보 탐색 스크립트 확장
- **작업 목적:** 네이버 크롤링을 대체할 KIS 수급 API 후보를 찾기 위해 검증 스크립트가 여러 endpoint/TR 조합을 중단 없이 테스트하도록 확장했습니다.
- **영향을 받은 파일:** `test_kis_investor_api.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `ApiProbe` 구조를 추가해 여러 KIS API 후보를 순차 테스트할 수 있게 했습니다.
  - `foreign-institution-total`, `inquire-investor`, `investor-trend-estimate` 후보를 추가하고 HTTP 오류가 나도 다음 후보를 계속 검사하도록 변경했습니다.
  - 로컬 검증 결과 `foreign-institution-total` API에서 외국인/기관 순매수 금액 필드(`frgn_ntby_tr_pbmn`, `orgn_ntby_tr_pbmn`)가 정상 응답하는 것을 확인했습니다.
---

## [2026-05-28 23:55] KIS 투자자 수급 API 검증 스크립트 추가
- **작업 목적:** 네이버 크롤링을 KIS API 기반 수급 데이터로 대체할 수 있는지 안전하게 확인하기 위한 독립 검증 스크립트를 추가했습니다.
- **영향을 받은 파일:** `test_kis_investor_api.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `test_kis_investor_api.py`를 추가해 KIS 토큰 발급과 `investor-trend` API 호출을 분리 검증할 수 있게 했습니다.
  - API 키, 시크릿, 토큰은 출력하지 않고 응답 상태, 메시지, output 필드 구조와 첫 행 샘플만 출력하도록 구성했습니다.
  - 기존 자동화 실행 로직은 변경하지 않았습니다.
---

## [2026-05-28 23:51] KIS 거래량 순위 마켓 코드 정리
- **작업 목적:** GitHub Actions 실행 중 KIS 거래량 순위 API에서 유효하지 않은 `V` 시장 코드 호출로 경고가 발생하던 문제를 정리했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `get_market_data()`의 거래량 순위 조회 시장 코드를 `['J', 'V']`에서 `['J']`로 변경했습니다.
  - `V` 코드는 해당 KIS 거래량 순위 API에서 유효하지 않으므로 호출하지 않도록 했습니다.
---

## [2026-05-28 23:44] 수급 크롤링 실패 시 자동화 중단 방지
- **작업 목적:** 네이버 금융 수급 크롤링이 실패하거나 빈 결과를 반환해도 전체 자동화가 중단되지 않도록 했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `get_investor_data()`에서 수급 데이터가 없을 때 예외를 발생시키지 않고 `ticker`, `foreign_net`, `inst_net` 컬럼을 가진 빈 DataFrame을 반환하도록 변경했습니다.
  - 네이버 크롤링 실패 시 `[Warning]` 로그를 남기고 수급 금액을 0으로 대체해 거래대금 기반 수집/저장 흐름이 계속 진행되도록 했습니다.
  - 부분 수급 데이터가 들어온 경우에도 `foreign_net`, `inst_net` 컬럼이 항상 존재하도록 방어 로직을 추가했습니다.
---

## [2026-05-28 01:42] 크롤러 KST 기준 저장 및 데이터 품질 로그 추가
- **작업 목적:** GitHub Actions의 UTC 실행 환경에서도 수집 날짜와 세션명이 한국 시간 기준으로 저장되도록 하고, 앞으로 들어오는 데이터 품질을 Actions 로그에서 확인할 수 있게 했습니다.
- **영향을 받은 파일:** `crawler.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `ZoneInfo("Asia/Seoul")`을 사용해 `target_date`와 DB 저장 세션명을 KST 기준으로 계산하도록 변경했습니다.
  - `save_to_db()`에 카테고리별 저장 행 수, 빈 종목명 수, 가격 0건, 거래대금 0건을 출력하는 품질 로그를 추가했습니다.
  - 저장 대상이 0건이거나 주요 값이 비어 있는 경우 `[Warning]` 로그를 남기도록 했습니다.
---

## [2026-05-28 01:30] 세션 시간순 정렬 적용
- **작업 목적:** 자동 실행 후 텔레그램 브리핑과 웹 대시보드가 세션명을 문자열순으로 정렬해 최신/이전 세션을 잘못 판단할 수 있는 문제를 해결했습니다.
- **영향을 받은 파일:** `analyzer.py`, `app.py`, `ai_changelog.md`
- **주요 변경 사항:**
  - `StockAnalyzer._session_sort_key()`를 추가해 세션명 괄호 안의 `HH:MM` 값을 기준으로 최신 세션과 이전 세션을 판단하도록 변경했습니다.
  - Streamlit 대시보드에도 `session_sort_key()`를 추가해 사이드바 세션 선택과 직전 세션 비교가 실제 시간순으로 동작하도록 변경했습니다.
---

## [2026-05-28 01:21] GitHub Actions KIS API 환경변수 연결
- **작업 목적:** 자동 실행 환경에서 KIS API 인증값이 `main.py` 실행 단계로 전달되지 않아 크롤링이 실패할 수 있는 문제를 해결했습니다.
- **영향을 받은 파일:** `.github/workflows/main.yml`, `ai_changelog.md`
- **주요 변경 사항:**
  - GitHub Actions의 메인 자동화 실행 step `env` 블록에 `KIS_APP_KEY`, `KIS_APP_SECRET` secret 전달 설정을 추가했습니다.
  - 기존 텔레그램 secret 전달 및 실행 흐름은 그대로 유지했습니다.
---

## [2026-05-28 01:10] 로컬 실행 환경 복구
- **작업 목적:** 깨진 가상환경을 현재 설치된 Python 3.10 기준으로 복구하고, 프로젝트 실행에 필요한 패키지 import 및 분석 모듈 실행이 가능하도록 정비했습니다.
- **영향을 받은 파일:** `venv/`, `ai_changelog.md`
- **주요 변경 사항:**
  - `venv`의 Python 기준 경로를 현재 사용 가능한 Python 3.10 환경으로 갱신했습니다.
  - `requirements.txt` 기준 패키지 설치 상태를 확인하고, Python 3.11용 바이너리가 남아 있던 주요 패키지(`numpy`, `pandas`, `pyarrow`, `matplotlib` 등)를 Python 3.10용으로 강제 재설치했습니다.
  - 핵심 패키지 import, 주요 Python 파일 문법 검사, `StockAnalyzer.run_analysis()` 실행, Streamlit 버전 확인으로 로컬 실행 환경을 검증했습니다.
---

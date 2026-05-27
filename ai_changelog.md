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

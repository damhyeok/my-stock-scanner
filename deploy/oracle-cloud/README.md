# Oracle Cloud Always Free 배포

이 구성은 개인 PC와 GitHub Actions 예약 실행에 의존하지 않고 Oracle Cloud 무료 VM에서 KIS 프로그램 매매를 실시간 수집합니다.

WebSocket 원시값은 VM의 `program_snapshots.db`에만 저장하고, 시장강도 계산 결과만 기존 `stock_data.db`에 반영해 09:30 종목 분석과의 SQLite 충돌을 방지합니다.

## 1. 무료 VM 생성

Oracle Cloud에서 `Always Free Eligible` 표시가 있는 VM을 생성합니다.

- 이미지: Ubuntu 22.04 또는 24.04
- Shape: `VM.Standard.E2.1.Micro` 권장 또는 Always Free 범위의 `VM.Standard.A1.Flex`
- 네트워크: 공용 IPv4 할당
- 보안 규칙: SSH 22번만 허용
- Home Region의 Always Free 자원만 사용

Oracle은 7일 동안 CPU와 네트워크 사용률이 모두 낮은 Always Free 인스턴스를 회수할 수 있습니다. 인스턴스 상태와 타이머 실행 여부를 정기적으로 확인해야 합니다.

## 2. GitHub SSH 쓰기 권한 설정

VM에서 SSH 키를 만들고 공개키를 GitHub 저장소의 `Settings > Deploy keys`에 `Allow write access`로 등록합니다.

```bash
ssh-keygen -t ed25519 -C "stock-scanner-cloud"
cat ~/.ssh/id_ed25519.pub
git clone git@github.com:damhyeok/my-stock-scanner.git ~/my-stock-scanner
```

## 3. 비밀값 설정

프로젝트 루트에 `.env`를 만들고 아래 값을 입력합니다. `.env`는 Git에 올리지 않습니다.

```dotenv
KIS_APP_KEY=...
KIS_APP_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ORACLE_TRIGGER_SECRET=충분히_긴_무작위_문자열
```

## 4. 무료 VM 타이머 설치

```bash
cd ~/my-stock-scanner
chmod +x deploy/oracle-cloud/setup.sh
./deploy/oracle-cloud/setup.sh
```

웹 대시보드에는 VM과 동일한 `ORACLE_TRIGGER_SECRET`과 아래 주소를 설정합니다.

```toml
ORACLE_TRIGGER_URL = "http://161.33.27.132:8765"
ORACLE_TRIGGER_SECRET = "VM과_동일한_값"
```

Oracle VCN 보안 목록의 수신 규칙에서 TCP `8765` 포트를 허용해야 합니다. 요청은 HMAC 서명,
5분 시각 제한, 일회용 nonce로 검증되며 비밀값 자체는 네트워크로 전송하지 않습니다.

설치 스크립트는 메모리 1GB인 E2 Micro에서도 Python 패키지 설치와 분석이 안정적으로 진행되도록 2GB swap 파일을 함께 구성합니다.

설치되는 KST 작업은 다음과 같습니다.

- 09:10: 오전 프로그램 매매 WebSocket 연결
- 09:30: 전체 종목 분석
- 09:50: 전체 종목 분석 및 오전 시장강도 계산
- 11:30: 전체 종목 분석
- 13:25: 오후 프로그램 매매 WebSocket 연결
- 14:00: 전체 종목 분석
- 14:01: 오후 시장강도 계산
- 14:20: 종가 프로그램 매매 WebSocket 연결
- 15:40: 종가 시장강도 계산 후 바닥후보 데이터 및 신호 갱신
- 16:00: 정규장 전체 분석

섹터 순환매 분봉 집계는 11:05·13:05·15:05·15:35에 실행해 각각
09:00~11:00·11:00~13:00·13:00~15:00·15:00~15:30 구간을 저장합니다.

## 5. 상태 확인

```bash
systemctl list-timers --all | grep -E 'morning-|afternoon-|closing-|sector-flow|stock-analysis'
journalctl -u 'stock-scanner@*' --since today
```

자동 예약은 Oracle 타이머만 사용하며 GitHub Actions는 수동 실행용으로만 유지합니다.

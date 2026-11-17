# 인터넷이 될 때 미리 챙길 것 (오프라인 꾸러미)

> 비행기 모드·산속·지하로 들어가기 **전에** 이 체크리스트를 끝내세요. 한 번 끝내면 그다음부터는 인터넷이 필요 없습니다.

## ✅ 허브(컴퓨터)

- [ ] Ollama 설치
- [ ] 모델 내려받기: `gemma4:e4b`(또는 e2b), `bge-m3`, (선택) `lfm3-audio:2b`
- [ ] 허브 파이썬 부품 설치 (`uv pip install -e .`)
- [ ] 내 문서 색인: `nunchi ingest <폴더>`
- [ ] `nunchi doctor` 통과
- [ ] **인터넷을 끊고** 다시 `nunchi doctor` + `nunchi ask` 로 확인 (doctor가 "인터넷: 연결 안 됨 (오프라인 모드 OK)" 표시)

### 다른 컴퓨터로 옮겨 설치할 때 (완전 오프라인 설치)

인터넷 되는 컴퓨터에서 꾸러미를 만들고 USB로 옮깁니다.

```bash
./scripts/prepare_offline.sh ~/nunchi-bundle --with-models
```

이 스크립트가 하는 일:
1. 레포 사본 복사 (`.gitignore` 대상인 `.env`·지식베이스·가상환경은 빼고)
2. 파이썬 부품을 `wheelhouse/` 에 내려받기 (`pip download`, 약 12MB · 27개)
3. `--with-models`: `~/.ollama/models` 에서 **필요한 모델의 파일만** 골라 `ollama-models/` 에 담기 (예: gemma4:e4b-mlx 9.5GB + bge-m3 1.2GB + lfm3-audio:2b 2.1GB). 모델 폴더 전체(수십 GB)를 옮길 필요가 없어요.
4. 대상 컴퓨터용 순서서 `OFFLINE-INSTALL.md` 작성

오프라인 컴퓨터에서 (꾸러미 폴더 안):

```bash
mkdir -p ~/.ollama/models && cp -R ollama-models/. ~/.ollama/models/
```

```bash
python3.12 -m venv hub/.venv
```

```bash
hub/.venv/bin/pip install --no-index --find-links wheelhouse -e "hub[dev]"
```

> 파이썬 부품(numpy 등)은 운영체제·CPU·파이썬 버전마다 달라요. 같은 종류의 컴퓨터끼리 옮기고, 대상 컴퓨터 파이썬 버전이 다르면 `--python 3.11` 처럼 맞춰서 만드세요(uv 필요). 이 순서는 실제로 `--no-index`(인터넷 사용 안 함)로 설치해 테스트 38개 통과까지 확인했습니다.

## ✅ 공기계(폰)

- [ ] Meta AI 앱 설치·로그인, 안경 페어링, **펌웨어 최신으로**
- [ ] Meta AI 앱에서 **개발자 모드** 켜기 (설정 → 내 안경 → 개발자 모드)
- [ ] Nunchi 앱 설치 (Xcode/Android Studio로 빌드)
- [ ] Nunchi 앱에서 **Meta AI와 연결**(등록) — 인터넷 필수
- [ ] 안경 카메라 권한 **'항상 허용'** (한 번 허용이면 다음에 또 물어봄)
- [ ] 허브 주소·토큰 입력 후 **연결 테스트**
- [ ] (단독 모드) 허브에서 **지식베이스 받기** 또는 `kb.json` 넣기
- [ ] 한국어 **오프라인 음성 데이터**
  - iOS: 설정 → 손쉬운 사용 → 음성 콘텐츠 → 음성 → 한국어 (Yuna 등) 내려받기
  - iOS 단독 모드 받아쓰기: 설정 → 일반 → 키보드 → 받아쓰기 켜고 한국어 사용 → 인터넷 될 때 한 번 받아쓰기 사용
  - Android: 설정 → 텍스트 음성 변환 → 엔진 설정 → 음성 데이터 설치 → 한국어
- [ ] 그다음 비행기 모드 + 블루투스·와이파이 켜기 → [재난문자 방해 없이 쓰기](04-airplane-mode.md)

## ✅ 안경

- [ ] 충전 (배터리 10% 미만이면 일부 설치·연결이 실패)
- [ ] 펌웨어 업데이트 후 카메라가 안 되면 안경 재부팅 (펌웨어 v128 알려진 이슈)

## ✅ 네트워크 (허브 모드)

- [ ] 인터넷 선을 뺀 공유기 또는 여행용 공유기 준비 → [인터넷 없는 와이파이 만들기](05-offline-network.md)
- [ ] 허브 컴퓨터의 IP를 메모 (`nunchi doctor` 에 표시)

## 출발 전 5분 리허설

1. 허브: `nunchi serve --host 0.0.0.0 --token …`
2. 공기계: 비행기 모드 → 블루투스·와이파이 켬 → 오프라인 공유기 연결
3. 앱에서 음성 질문 1번, 사진 질문 1번
4. 공유기 전원을 꺼 보고 → 단독 모드로 전환해 질문 1번

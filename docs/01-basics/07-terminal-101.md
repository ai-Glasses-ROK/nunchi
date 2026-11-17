# 터미널 첫걸음 (개발이 처음인 분께)

> 이 문서만 읽으면 레포의 명령어를 복사·붙여넣기로 따라 할 수 있어요.

## 1. 터미널 열기

- **맥**: `⌘ + 스페이스` → "터미널" 입력 → 엔터
- **윈도우**: 시작 → "PowerShell"
- **리눅스**: `Ctrl + Alt + T`

까만(또는 흰) 창에 글자를 쳐서 컴퓨터에 일을 시키는 곳이에요.

## 2. 꼭 알아야 할 명령 8개

| 명령 | 뜻 | 예 |
|---|---|---|
| `pwd` | 지금 어느 폴더에 있나 | `/Users/me` |
| `ls` | 이 폴더에 뭐가 있나 (윈도우: `dir`) | |
| `cd 폴더` | 폴더로 들어가기 | `cd nunchi` |
| `cd ..` | 한 단계 위로 | |
| `mkdir 이름` | 폴더 만들기 | |
| `cat 파일` | 파일 내용 보기 | `cat README.md` |
| `Ctrl + C` | 실행 중인 프로그램 멈추기 | 서버 끌 때 |
| `↑` 화살표 | 방금 친 명령 다시 불러오기 | |

## 3. 경로(path) 읽는 법

```
/Users/me/nunchi/hub        ← 맨 앞 / 는 '컴퓨터의 뿌리'부터 (절대 경로)
../examples/knowledge       ← .. 는 '한 단계 위' (상대 경로)
~/nunchi                    ← ~ 는 '내 홈 폴더'
```

## 4. 이 레포 받기

```bash
git clone https://github.com/ai-Glasses-ROK/nunchi.git
```

```bash
cd nunchi
```

git이 없다면 GitHub 페이지의 **Code → Download ZIP** 으로 받아 압축을 풀어도 됩니다.

## 5. 파이썬 가상환경 (프로젝트 전용 공구함)

프로젝트마다 필요한 부품(라이브러리) 버전이 달라서, 각자 따로 담는 상자를 만듭니다. 이 레포는 `uv`를 권장해요.

```bash
cd hub
uv venv --python 3.12
```

```bash
uv pip install -e .
```

그다음부터는 `.venv/bin/nunchi`(윈도우: `.venv\Scripts\nunchi`)로 실행하거나, 아래처럼 '상자 열기'를 하면 그냥 `nunchi`로 실행돼요.

```bash
source .venv/bin/activate
```

## 6. 환경변수 = 프로그램 설정 메모

명령 앞에 `이름=값`을 붙이면 그 실행에만 설정이 바뀝니다.

```bash
NUNCHI_LLM_MODEL=gemma4:e2b nunchi ask "안녕"
```

자주 쓰는 설정은 `hub/.env` 파일에 적어 두세요(`hub/.env.example`을 복사).

## 7. 에러가 나면

1. 빨간 글자의 **마지막 줄**을 읽어요. 대부분 원인이 거기 있어요.
2. `nunchi doctor` 를 실행하면 무엇이 빠졌는지 한국어로 알려 줍니다.
3. [문제 해결](../06-reference/01-troubleshooting.md) 문서에서 같은 메시지를 찾아보세요.

## 8. 코드 블록 읽는 법 (이 문서들에서)

````
```bash
nunchi doctor          ← 이 줄을 복사해서 터미널에 붙여넣고 엔터
```
````

- `#` 로 시작하는 줄은 설명(주석)이라 실행되지 않아요.
- `<내맥IP>` 처럼 꺾쇠로 감싼 부분은 **내 값으로 바꿔** 넣으세요.

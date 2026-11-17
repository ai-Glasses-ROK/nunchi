#!/usr/bin/env bash
# Nunchi 허브 한 번에 설치하기 (macOS, 애플 실리콘 권장) — 인터넷이 될 때 실행하세요.
#
#   scripts/setup_mac.sh                 # 기본: 램에 맞춰 gemma4:e4b 또는 e2b + bge-m3
#   scripts/setup_mac.sh --with-tts      # 허브가 직접 한국어 음성도 만들기 (lfm3-audio:2b, 약 2GB)
#   scripts/setup_mac.sh --examples      # 예제 문서 3개를 색인해서 바로 질문해 보기
#   scripts/setup_mac.sh --model gemma4:e2b
#
# 하는 일: Ollama 확인 → 모델 내려받기 → 파이썬 가상환경(hub/.venv) → nunchi doctor
# 이미 끝난 단계는 건너뛰므로 여러 번 실행해도 안전합니다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HUB="$ROOT/hub"
OLLAMA_URL="${NUNCHI_OLLAMA_URL:-http://127.0.0.1:11434}"
MODEL=""
WITH_TTS=0
EXAMPLES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:?--model 뒤에 모델 이름}"; shift 2 ;;
    --with-tts) WITH_TTS=1; shift ;;
    --examples) EXAMPLES=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "모르는 옵션: $1 (--help 참고)"; exit 2 ;;
  esac
done

say_step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || fail "맥 전용 스크립트예요. 윈도우·리눅스는 docs/03-setup/01-hub.md 를 따라 주세요."

# ── 1. 컴퓨터 사양 ─────────────────────────────────────────
ARCH="$(uname -m)"
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
say_step "컴퓨터: $ARCH · 메모리 ${RAM_GB}GB"
if [[ -z "$MODEL" ]]; then
  if (( RAM_GB < 16 )); then MODEL="gemma4:e2b"; else MODEL="gemma4:e4b"; fi
fi
PULL_MODEL="$MODEL"
if [[ "$ARCH" == "arm64" && "$MODEL" == gemma4:* && "$MODEL" != *-mlx ]]; then
  PULL_MODEL="${MODEL}-mlx"   # 애플 실리콘 전용 빌드가 더 빠름 (nunchi가 자동으로 이 이름을 씀)
fi
echo "답변·음성인식 모델: $MODEL (내려받을 이름: $PULL_MODEL)"

# ── 2. Ollama ─────────────────────────────────────────────
say_step "Ollama 확인"
if ! command -v ollama >/dev/null 2>&1; then
  cat <<'MSG'
Ollama가 없어요. 둘 중 하나로 설치한 뒤 이 스크립트를 다시 실행하세요.
  1) https://ollama.com/download 에서 맥 앱 설치 (권장)
  2) brew install --cask ollama
MSG
  exit 1
fi
ollama --version || true
if ! curl -sf "$OLLAMA_URL/api/version" >/dev/null; then
  echo "Ollama가 꺼져 있어요. 앱을 켭니다…"
  open -a Ollama 2>/dev/null || (nohup ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 30); do
    curl -sf "$OLLAMA_URL/api/version" >/dev/null && break
    sleep 1
  done
  curl -sf "$OLLAMA_URL/api/version" >/dev/null || fail "Ollama가 응답하지 않아요 ($OLLAMA_URL). 메뉴바에서 Ollama를 켠 뒤 다시 실행하세요."
fi
echo "✅ Ollama 응답함 ($OLLAMA_URL)"

# ── 3. 모델 내려받기 ──────────────────────────────────────
pull() {
  if ollama list | awk 'NR>1{print $1}' | grep -qx -e "$1" -e "$1:latest"; then
    echo "✅ $1 이미 있음"
  else
    say_step "$1 내려받는 중 (처음 한 번만, 수 GB)"
    ollama pull "$1"
  fi
}
pull "$PULL_MODEL"
pull "bge-m3"
(( WITH_TTS )) && pull "lfm3-audio:2b"

# ── 4. 파이썬 가상환경 ────────────────────────────────────
say_step "파이썬 가상환경 (hub/.venv)"
cd "$HUB"
if command -v uv >/dev/null 2>&1; then
  [[ -x .venv/bin/python ]] || uv venv --python 3.12
  uv pip install --python .venv/bin/python -e ".[dev]"
else
  PY="$(command -v python3 || true)"
  [[ -n "$PY" ]] || fail "python3이 없어요. 'xcode-select --install' 또는 https://www.python.org 에서 설치하세요 (3.10 이상)."
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "파이썬 3.10 이상이 필요해요 (지금: $("$PY" --version)). uv 설치를 권장: curl -LsSf https://astral.sh/uv/install.sh | sh"
  [[ -x .venv/bin/python ]] || "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e ".[dev]"
fi

# ── 5. 설정 파일 ──────────────────────────────────────────
if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i '' "s|^NUNCHI_LLM_MODEL=[^ ]*|NUNCHI_LLM_MODEL=$MODEL|" .env
  echo "✅ hub/.env 만들었어요 (모델: $MODEL)"
else
  echo "ℹ️  hub/.env 가 이미 있어서 그대로 둡니다"
fi

# ── 6. 예제 문서 (선택) ───────────────────────────────────
if (( EXAMPLES )); then
  say_step "예제 문서 색인"
  .venv/bin/nunchi ingest "$ROOT/examples/knowledge"
fi

# ── 7. 점검 ───────────────────────────────────────────────
say_step "nunchi doctor"
.venv/bin/nunchi doctor || true

cat <<MSG

다음 단계 (hub 폴더에서):
  .venv/bin/nunchi ask "질문"                 # 글자로 묻기
  .venv/bin/nunchi ingest ~/Documents/내문서   # 내 문서 넣기
  .venv/bin/nunchi serve --host 0.0.0.0 --token 비밀번호   # 폰·안경에서 쓰기
안내서: docs/README.md
MSG

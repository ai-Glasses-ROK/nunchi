#!/usr/bin/env bash
# 인터넷 없는 컴퓨터로 옮길 "오프라인 꾸러미" 만들기 — 인터넷이 되는 컴퓨터에서 실행하세요.
#
#   scripts/prepare_offline.sh ~/nunchi-bundle                    # 레포 사본 + 파이썬 부품(wheelhouse)
#   scripts/prepare_offline.sh /Volumes/USB/nunchi --with-models  # + Ollama 모델 (필요한 것만 골라서)
#   scripts/prepare_offline.sh DIR --with-models --models gemma4:e2b-mlx,bge-m3
#   scripts/prepare_offline.sh DIR --python 3.11                  # 대상 컴퓨터의 파이썬 버전에 맞추기 (uv 필요)
#
# 만들어지는 것:
#   DIR/                    레포 사본 (커밋된 파일 + 커밋 전 파일, .gitignore 대상은 제외)
#   DIR/wheelhouse/         파이썬 부품 (.whl, 약 12MB)
#   DIR/ollama-models/      (--with-models) 고른 모델만 — 대상 컴퓨터의 ~/.ollama/models 에 합치면 끝
#   DIR/OFFLINE-INSTALL.md  대상 컴퓨터에서 따라 할 순서
#
# 주의: 파이썬 부품(numpy 등)은 운영체제·CPU·파이썬 버전마다 다릅니다.
#       같은 종류의 컴퓨터(예: 애플 실리콘 맥 → 애플 실리콘 맥)끼리 옮기세요.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST=""
WITH_MODELS=0
MODELS=""
PYVER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-models) WITH_MODELS=1; shift ;;
    --models) MODELS="${2:?--models 뒤에 모델 이름들 (쉼표로 구분)}"; shift 2 ;;
    --python) PYVER="${2:?--python 뒤에 버전 (예: 3.12)}"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "모르는 옵션: $1 (--help 참고)"; exit 2 ;;
    *) DEST="$1"; shift ;;
  esac
done

say_step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

[[ -n "$DEST" ]] || fail "꾸러미를 만들 폴더를 알려 주세요. 예) scripts/prepare_offline.sh ~/nunchi-bundle"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
case "$DEST/" in
  "$ROOT"/*) fail "꾸러미 폴더는 레포 바깥이어야 해요 (지금: $DEST)" ;;
esac
[[ -z "$(ls -A "$DEST")" ]] || fail "빈 폴더를 지정해 주세요 (이미 파일이 있음: $DEST)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── 1. 레포 사본 ──────────────────────────────────────────
say_step "레포 사본 → $DEST"
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  (
    cd "$ROOT"
    git ls-files -z -co --exclude-standard |
      while IFS= read -r -d '' f; do [[ -f "$f" ]] && printf '%s\0' "$f"; done |
      tar --null -T - -cf -
  ) | tar -xf - -C "$DEST"
else
  tar -C "$ROOT" --exclude .git --exclude .venv --exclude .env --exclude 'hub/data' \
    --exclude '*.sqlite3*' --exclude wheelhouse --exclude DerivedData --exclude .gradle \
    --exclude __pycache__ -cf - . | tar -xf - -C "$DEST"
fi
echo "✅ $(find "$DEST" -type f | wc -l | tr -d ' ')개 파일"

# ── 2. 파이썬 부품 (wheelhouse) ───────────────────────────
say_step "파이썬 부품 내려받기"
if command -v uv >/dev/null 2>&1; then
  if [[ -z "$PYVER" && -x "$ROOT/hub/.venv/bin/python" ]]; then
    PYVER="$("$ROOT/hub/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  fi
  PYVER="${PYVER:-3.12}"
  uv venv -q --seed --python "$PYVER" "$TMP/venv"   # pip가 들어 있는 임시 환경
  PY="$TMP/venv/bin/python"
else
  [[ -z "$PYVER" ]] || fail "--python 은 uv가 필요해요: curl -LsSf https://astral.sh/uv/install.sh | sh"
  PY="$(command -v python3 || true)"
  [[ -n "$PY" ]] || fail "python3이 없어요 (3.10 이상 필요)"
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "파이썬 3.10 이상이 필요해요"
fi
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PLATFORM="$("$PY" -c 'import platform; print(platform.system(), platform.machine())')"
"$PY" -m pip download -q --dest "$DEST/wheelhouse" "$ROOT/hub[dev]" "setuptools>=77" wheel
echo "✅ $(ls "$DEST/wheelhouse" | wc -l | tr -d ' ')개 · $(du -sh "$DEST/wheelhouse" | cut -f1) (파이썬 $PYVER · $PLATFORM 용)"

# ── 3. Ollama 모델 (선택) ─────────────────────────────────
MODELS_SRC="${OLLAMA_MODELS:-$HOME/.ollama/models}"
COPIED_MODELS=""
if (( WITH_MODELS )); then
  say_step "Ollama 모델 골라 담기 ($MODELS_SRC)"
  [[ -d "$MODELS_SRC/manifests" ]] || fail "Ollama 모델 폴더를 못 찾았어요: $MODELS_SRC (OLLAMA_MODELS 로 지정 가능)"
  if [[ -z "$MODELS" ]]; then
    LLM=""
    if [[ -f "$ROOT/hub/.env" ]]; then
      LLM="$(sed -n 's/^NUNCHI_LLM_MODEL=\([^ #]*\).*/\1/p' "$ROOT/hub/.env" | head -1)"
    fi
    MODELS="${LLM:-gemma4:e4b},bge-m3,lfm3-audio:2b?"   # ? = 없으면 건너뜀
  fi
  # 모델 이름 → 매니페스트·블롭 목록 (필요한 파일만 복사해서 수십 GB를 아낌)
  "$PY" - "$MODELS_SRC" "$MODELS" > "$TMP/files.txt" 2> "$TMP/models.txt" <<'PYEOF'
import json, sys
from pathlib import Path

root, wanted = Path(sys.argv[1]), sys.argv[2]
for raw in filter(None, (w.strip() for w in wanted.split(","))):
    optional = raw.endswith("?")
    name = raw.rstrip("?")
    base, _, tag = name.partition(":")
    tag = tag or "latest"
    path = base if "/" in base else f"library/{base}"
    candidates = [(path, tag)]
    if not tag.endswith("-mlx"):
        candidates.insert(0, (path, f"{tag}-mlx"))  # 애플 실리콘 전용 빌드가 있으면 그쪽
    for p, t in candidates:
        manifest = root / "manifests" / "registry.ollama.ai" / p / t
        if manifest.is_file():
            break
    else:
        if not optional:
            print(f"⚠️  설치되지 않은 모델: {name} (ollama pull {name})", file=sys.stderr)
        continue
    data = json.loads(manifest.read_text())
    digests = [data["config"]["digest"], *[layer["digest"] for layer in data["layers"]]]
    size = sum(layer.get("size", 0) for layer in data["layers"])
    print(manifest.relative_to(root))
    for d in digests:
        blob = f"blobs/{d.replace(':', '-')}"
        if (root / blob).is_file():
            print(blob)
        meta = root / "metadata" / f"{d.replace(':', '-')}.json"
        if meta.is_file():
            print(meta.relative_to(root))
    v2 = root / "manifests-v2" / "ollama.com" / p / t
    if v2.is_file():
        print(v2.relative_to(root))
    label = p.removeprefix("library/") + ":" + t
    print(f"✅ {label}  ({size / 1e9:.1f} GB)", file=sys.stderr)
PYEOF
  cat "$TMP/models.txt"
  [[ -s "$TMP/files.txt" ]] || fail "담을 모델이 없어요"
  while IFS= read -r rel; do
    mkdir -p "$DEST/ollama-models/$(dirname "$rel")"
    # 같은 디스크면 APFS 복제(cp -c)로 순식간에, 아니면 일반 복사
    cp -c "$MODELS_SRC/$rel" "$DEST/ollama-models/$rel" 2>/dev/null || cp "$MODELS_SRC/$rel" "$DEST/ollama-models/$rel"
  done < "$TMP/files.txt"
  COPIED_MODELS="$(grep '^✅' "$TMP/models.txt" | sed 's/^✅ /- /')"
  echo "✅ 모델 폴더 $(du -sh "$DEST/ollama-models" | cut -f1)"
fi

# ── 4. 설치 안내서 ────────────────────────────────────────
say_step "OFFLINE-INSTALL.md 작성"
{
  echo "# Nunchi 오프라인 설치 순서"
  echo
  echo "이 꾸러미는 $(date +%Y-%m-%d)에 만들어졌어요. 파이썬 부품은 **파이썬 $PYVER · $PLATFORM** 용입니다."
  echo
  echo "## 1. Ollama"
  echo
  echo "- Ollama 설치 파일은 꾸러미에 없어요. 인터넷 될 때 https://ollama.com/download 에서 받아 함께 옮기세요."
  if [[ -n "$COPIED_MODELS" ]]; then
    echo "- 모델 합치기 (기존 모델은 그대로 남아요):"
    echo
    echo '```bash'
    echo 'mkdir -p ~/.ollama/models && cp -R ollama-models/. ~/.ollama/models/'
    echo '```'
    echo
    echo "  들어 있는 모델:"
    echo
    echo "$COPIED_MODELS" | sed 's/^/  /'
    echo
    echo "- Ollama를 껐다 켠 뒤 \`ollama list\` 에 보이면 성공. 안 보이면 Ollama 버전을 원래 컴퓨터와 맞추거나, 원래 컴퓨터의 ~/.ollama/models 폴더를 통째로 복사하세요."
  else
    echo "- 모델은 꾸러미에 없어요 (\`--with-models\` 로 다시 만들거나 ~/.ollama/models 폴더를 통째로 복사)."
  fi
  echo
  echo "## 2. 허브 설치 (인터넷 없이)"
  echo
  echo '```bash'
  echo "python$PYVER -m venv hub/.venv"
  echo '```'
  echo
  echo '```bash'
  echo 'hub/.venv/bin/pip install --no-index --find-links wheelhouse -e "hub[dev]"'
  echo '```'
  echo
  echo '```bash'
  echo 'cd hub && .venv/bin/nunchi doctor'
  echo '```'
  echo
  echo "## 3. 확인"
  echo
  echo "- \`nunchi doctor\` 가 \"인터넷: 연결 안 됨 (오프라인 모드 OK)\" 와 함께 모두 정상인지"
  echo "- \`nunchi ingest ../examples/knowledge\` → \`nunchi ask \"발전기 E2 오류는?\"\`"
  echo "- 자세한 안내: docs/03-setup/02-offline-bundle.md"
} > "$DEST/OFFLINE-INSTALL.md"

say_step "끝"
du -sh "$DEST" | awk '{print "꾸러미 크기: " $1}'
echo "다음: $DEST 폴더를 USB 등으로 옮긴 뒤 OFFLINE-INSTALL.md 를 따라 하세요."

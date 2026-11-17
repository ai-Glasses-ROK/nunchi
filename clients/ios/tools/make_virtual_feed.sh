#!/usr/bin/env bash
# 가상 안경 카메라 피드(HEVC mp4)를 다시 만듭니다. 필요: macOS(swift), ffmpeg(libx265)
#   ./tools/make_virtual_feed.sh
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
swift tools/make_virtual_feed.swift "$tmp/panel.png"
# MockDeviceKit은 H.265(HEVC) 영상을 받습니다. 애플 기기 호환을 위해 hvc1 태그를 붙입니다.
ffmpeg -hide_banner -loglevel error -y -loop 1 -i "$tmp/panel.png" -t 3 -r 15 \
  -vf "scale=720:1280,format=yuv420p" -c:v libx265 -tag:v hvc1 -x265-params log-level=error \
  Nunchi/Resources/virtual-glasses-feed.mp4
ls -la Nunchi/Resources/virtual-glasses-feed.mp4

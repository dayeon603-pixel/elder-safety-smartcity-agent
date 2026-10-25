#!/usr/bin/env bash
# 데모 보조 도구.
#   bash ~/kcf/tools/demo_tools.sh lan     → 관제/현장 단말 LAN 주소 + 단말 QR (같은 와이파이)
#   bash ~/kcf/tools/demo_tools.sh record  → 백업 데모 화면 녹화 시작 (q로 종료)
#   bash ~/kcf/tools/demo_tools.sh awake   → 심사 끝날 때까지 잠자기 방지
set -euo pipefail

PORT="${PORT:-5190}"
OUT_DIR="$HOME/kcf/deck/demo_backup"

case "${1:-}" in
  lan)
    ip=$(ipconfig getifaddr en0 || true)
    [ -n "$ip" ] || { echo "와이파이 IP 없음"; exit 1; }
    base="http://$ip:$PORT"
    # 기본 정류장: API의 추천 1위 (관악구). API가 꺼져 있으면 ?stop= 없이 열어도 페이지가 같은 규칙으로 고른다.
    stop=$(curl -fsS -m 5 -G "http://127.0.0.1:$PORT/api/mobility/stops" --data-urlencode "gu=관악구" --data-urlencode "limit=1" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["items"][0]["stop_id"])' 2>/dev/null || true)
    q=""; [ -z "$stop" ] || q="?stop=$stop"
    echo "관제 화면 (노트북)   $base/twin"
    echo "현장 단말 (폰/태블릿) $base/terminal$q"
    echo "노트북 대체 창        http://localhost:$PORT/terminal$q   (같은 화면, 새 브라우저 창)"
    echo "(Vite는 server.host=true 라 npm run dev 만으로 폰에서 열립니다. 폰 화면에서 '시작'을 한 번 누르세요.)"
    echo "(HTTP 라 폰 브라우저의 '화면 켜짐 유지'는 안 됩니다: 폰의 자동 잠금을 '안 함'으로 두세요.)"
    qrencode -t ANSIUTF8 "$base/terminal$q"
    ;;
  record)
    mkdir -p "$OUT_DIR"
    f="$OUT_DIR/demo_$(date +%H%M%S).mp4"
    # 화면 장치 번호는 'ffmpeg -f avfoundation -list_devices true -i ""'로 확인. 기본 1 = 주 화면.
    # 첫 실행 시 시스템 설정 > 개인정보 보호 > 화면 기록에서 터미널 허용 필요.
    echo "녹화: $f  (끝내려면 q)"
    ffmpeg -loglevel error -f avfoundation -framerate 30 -capture_cursor 1 -i "${SCREEN:-1}:none" \
      -vf "scale=1920:-2" -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p "$f"
    echo "저장됨: $f"
    ;;
  awake)
    echo "잠자기 방지 중 (Ctrl+C로 해제)"
    caffeinate -dimsu
    ;;
  *)
    sed -n 2,5p "$0"; exit 1 ;;
esac

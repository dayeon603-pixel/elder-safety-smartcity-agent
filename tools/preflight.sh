#!/usr/bin/env bash
# 영점 현장 사전점검. 출발 전, 도착 직후, 심사 직전에 한 번씩 돌린다.
# 사용: bash ~/kcf/tools/preflight.sh  (실패 항목이 있으면 exit 1)
set -uo pipefail

KCF="$HOME/kcf"
APP="$KCF/app"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-5190}"
DOCKER_PORT="${DOCKER_PORT:-8190}"
FAIL=0

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=1; }

echo "== 도구"
for t in uv node npm sqlite3 docker docker-compose colima dot soffice pdftoppm ffmpeg qrencode claude; do
  command -v "$t" >/dev/null 2>&1 && ok "$t" || bad "${t} 없음"
done

echo "== Docker"
if perl -e "alarm 15; exec @ARGV" colima status >/dev/null 2>&1; then ok "colima 실행 중"; else
  warn "colima 꺼짐 → 시작합니다"; colima start --cpu 4 --memory 6 >/dev/null 2>&1 && ok "colima 시작됨" || bad "colima 시작 실패"
fi
for img in $(cd "$APP" && perl -e "alarm 20; exec @ARGV" docker-compose config --images 2>/dev/null); do
  docker image inspect "$img" >/dev/null 2>&1 && ok "이미지 캐시: $img" || bad "이미지 없음: $img (와이파이 있을 때 make docker)"
done

echo "== 포트"
for p in "$BACKEND_PORT" "$FRONTEND_PORT" "$DOCKER_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    warn ":${p} 사용 중 ($(lsof -nP -iTCP:"$p" -sTCP:LISTEN | awk 'NR==2{print $1" pid "$2}'))"
  else ok ":${p} 비어 있음"; fi
done

echo "== 코드"
[ -f "$APP/.env" ] && ok ".env 있음" || bad ".env 없음 (cd $APP && make env)"
if (cd "$APP/backend" && uv run --offline pytest -q -p no:cacheprovider >/dev/null 2>&1); then ok "pytest 통과 (오프라인)"; else bad "pytest 실패: cd $APP/backend && uv run pytest -q -p no:cacheprovider"; fi
[ -d "$APP/frontend/node_modules" ] && ok "node_modules 설치됨" || bad "node_modules 없음 (npm ci)"
[ -f "$APP/frontend/dist/index.html" ] && ok "프론트 빌드 있음" || warn "프론트 빌드 없음 (npm run build)"

echo "== 데이터"
n=$(find "$KCF/data/cache" -type f 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -ge 60 ] && ok "캐시 데이터 ${n}개" || bad "캐시 데이터 ${n}개 (bash $KCF/data/fetch_all.sh)"

echo "== 노트북"
batt=$(pmset -g batt | grep -Eo '[0-9]+%' | head -1)
src=$(pmset -g batt | head -1 | grep -o "'.*'")
[ "${batt%\%}" -ge 60 ] && ok "배터리 ${batt} ${src}" || warn "배터리 ${batt} ${src} → 충전기 연결"
free_gb=$(df -g "$HOME" | awk 'NR==2{print $4}')
[ "$free_gb" -ge 20 ] && ok "디스크 여유 ${free_gb}GB" || warn "디스크 여유 ${free_gb}GB"
if curl -s -m 4 -o /dev/null https://api.anthropic.com; then ok "인터넷 연결 (Claude 사용 가능)"; else warn "인터넷 없음: Claude 불가, 앱·데이터는 오프라인으로 동작"; fi

echo "== 대회 규정 (외부 소통 금지)"
for app in KakaoTalk Discord Slack Messages Mail; do
  pgrep -xq "$app" && warn "${app} 실행 중 → 종료 권장" || ok "${app} 꺼짐"
done
grep -qs '"mcp__claude_ai_Gmail"' "$KCF/.claude/settings.json" && ok "Gmail/Drive/Calendar/Docs MCP 차단됨 (~/kcf 세션)" || warn "MCP 차단 미적용: cp ~/kcf/tools/settings.proposed.json ~/kcf/.claude/settings.json"

echo
[ "$FAIL" -eq 0 ] && echo "사전점검 통과" || echo "실패 항목을 먼저 고치세요"
exit "$FAIL"

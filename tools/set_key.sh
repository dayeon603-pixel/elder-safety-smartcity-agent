#!/usr/bin/env bash
# API 키를 macOS 키체인(암호화 저장)에 넣는다. 키는 대화·셸 기록·파일 어디에도 평문으로 남지 않는다.
# 사용: bash ~/kcf/tools/set_key.sh DATA_GO_KR_KEY   (화면에 숨김 입력 대화상자가 뜬다)
# 읽기: security find-generic-password -a kcf -s DATA_GO_KR_KEY -w
set -euo pipefail

NAME="${1:?키 이름을 주세요 (예: DATA_GO_KR_KEY, KMA_APIHUB_KEY)}"
case "$NAME" in
  DATA_GO_KR_KEY|KMA_APIHUB_KEY|KMA_APIHUB_WRN_URL|SAFETYDATA_KEY|VWORLD_KEY) ;;
  *) echo "허용되지 않은 이름: $NAME" >&2; exit 2 ;;
esac

VAL=$(osascript -e "text returned of (display dialog \"$NAME 붙여넣기 (화면에 보이지 않습니다)\" default answer \"\" with hidden answer buttons {\"취소\", \"저장\"} default button \"저장\" with title \"영점 · 키 저장\")" 2>/dev/null) || { echo "취소됨"; exit 1; }
VAL=$(printf '%s' "$VAL" | tr -d '[:space:]')
[ -n "$VAL" ] || { echo "빈 값이라 저장하지 않음"; exit 1; }

# -U: 이미 있으면 갱신. 값은 인자로 넘기지 않고 -w 뒤 표준입력 대신 변수로 전달(프로세스 목록 노출 최소화를 위해 즉시 unset).
security add-generic-password -U -a kcf -s "$NAME" -w "$VAL" >/dev/null
echo "$NAME 저장됨 (키체인, 길이 ${#VAL})"
unset VAL

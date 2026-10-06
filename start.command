#!/bin/sh
# Double-click on macOS to start a task without changing shell settings.
tool_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$tool_dir" || exit 1
echo 'TernPilot — 한 번의 요청, 가장 적합한 AI.'
echo '구현은 Astra, 설계·중요한 리뷰는 Opus, 기획·요약은 Gemini Pro.'
echo '각 CLI에 로그인한 상태여야 합니다. 작업은 각 계정의 사용량을 소비합니다.'
printf '\n작업할 Git 저장소의 전체 경로: '
IFS= read -r repo || exit 1
case "$repo" in '') echo '저장소 경로가 필요합니다.'; exit 2 ;; esac
printf '할 작업: '
IFS= read -r task || exit 1
case "$task" in '') echo '작업 내용이 필요합니다.'; exit 2 ;; esac
./dev --repo "$repo" -- "$task"
result=$?
printf '\n종료 코드: %s. Enter를 누르면 창을 닫을 수 있습니다.\n' "$result"
IFS= read -r ignored
exit "$result"

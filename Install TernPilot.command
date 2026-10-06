#!/bin/sh
cd "$(dirname "$0")" || exit 1
echo 'TernPilot — Mac 설치'
echo '실행 파일과 앱을 이 Mac의 사용자 폴더에 설치합니다.'
echo 'Codex 전역 위임을 켜면 관련 요청·코드·요약을 Claude/Google에 전달할 수 있습니다.'
echo '비밀값과 무관한 대화는 제외하고 기존 프로젝트 제한과 승인 규칙을 따릅니다.'
printf '설치와 함께 새 Codex 대화의 전역 위임도 켤까요? [y/N]: '
IFS= read -r answer
case "$answer" in y|Y|yes|YES) python3 install.py --enable-codex ;; *) python3 install.py ;; esac
result=$?
if [ "$result" -eq 0 ]; then
  echo '설치 완료. 전역 위임을 켰다면 새 Codex 대화부터 적용됩니다.'
else
  echo '설치가 중단되었습니다. 위 오류를 확인하세요.'
fi
printf '\n종료 코드 %s. Enter를 누르면 창을 닫을 수 있습니다.\n' "$result"
IFS= read -r ignored
exit "$result"

# TernPilot Desktop

macOS 앱: `~/Applications/TernPilot.app`. 별도 브라우저 링크 없이 독립 창에서 실행합니다.

- 기존 대화, Obsidian 저장 위치, 프로젝트와 CLI 계정은 유지합니다.
- 계정 및 연결에서 ChatGPT(Codex), Claude Code, Gemini(설치된 Antigravity)의 로그인 도구를 엽니다. 사용자 본인이 공식 도구에서 로그인을 완료합니다. 비밀번호나 OAuth 토큰을 TernPilot에 입력하지 않습니다.
- Gemini 연결은 Antigravity 도구와 해당 계정의 모델 이용 자격이 필요합니다. 일반 웹 구독과 API 사용량은 합쳐지지 않습니다.
- 자동 선택은 기존 Astra 분류기를 사용합니다. 선택한 제공자와 실제 실행된 제공자가 다르면 답변 아래에 전환을 표시합니다. 새 실행부터 지정된 모델 ID도 기록합니다. 과거 기록에 없는 모델 ID는 추정하지 않습니다.
- 프로젝트 작업 모드입니다. 기존 CLI 권한과 승인 설정을 유지하며 요청에 따라 파일 변경이 가능합니다. Gemini는 분석만 요청하지만 도구 자체의 읽기 전용 보장을 의미하지 않습니다.
- Markdown 제목, 표, 목록, 코드와 링크를 표시합니다. 답변의 HTML은 정제하며 외부 이미지/스크립트를 로드하지 않습니다.
- 사용량은 제공자의 조회 결과이며, 실패 시 자동 재시도합니다. 서버 실행 중 Codex/Gemini는 약 1분, Claude는 약 5분 주기로 확인합니다.
- 앱 창을 닫아도 진행 중 작업과 로컬 서버는 계속됩니다. 앱 다시 열기/보기 → 다시 연결로 기존 서버를 재사용합니다.

## 설치

Apple Command Line Tools(Python 3, Swift), Git 및 사용할 제공자의 공식 CLI가 필요합니다. 저장소에서 `python3 install.py`를 실행합니다. 처음 실행하는 Mac에서는 기존 Git 프로젝트를 선택합니다. 볼트 동기화와 다른 컴퓨터의 로그인은 별도입니다.

기존 설치의 네이티브 창만 갱신하려면 `python3 build_desktop.py --runtime /absolute/runtime/path`를 실행합니다. 로컬 빌드에는 개발용 ad-hoc 서명을 사용합니다. 공개 배포용 Apple 공증/서명 또는 Windows 패키지는 포함하지 않습니다.

Markdown 라이브러리는 로컬에 포함돼 오프라인에서도 표시할 수 있습니다. 버전, 출처와 라이선스는 `THIRD_PARTY_LICENSES.txt`에 있습니다.

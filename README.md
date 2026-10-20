# TernPilot · 턴파일럿

**한 번의 요청, 가장 적합한 AI.**

Local AI task routing across Codex, Claude Code and Gemini CLI.
**v0.4.0-preview.1 · macOS preview · Source available, all rights reserved.**

[Download the macOS preview](https://github.com/Jakelul/TernPilot/releases) · [Cloud development](CLOUD_START.md) · [Changelog](CHANGELOG.md) · [License](LICENSE)

TernPilot은 기존 Codex·Claude Code·Gemini CLI로 작업을 나누고 각 계정의 사용량을 사용합니다. 현재 Mac용 프리뷰이며 Python 표준 라이브러리만 사용합니다. 세 서비스의 토큰 잔액을 합치는 기능은 아닙니다.

## 사용

개발 버전은 **Astra 판단 → 선택된 엔진 실행**을 기본으로 사용합니다. Astra가 요청 내용을
읽고 Codex·Claude·Gemini 중 하나와 배정 이유를 반환하면, TernPilot이 결과를 검증한 뒤
원래 요청을 해당 CLI에 전달합니다. 한 요청을 여러 단계로 자동 분해하거나 계속 감독하는
기능은 이번 범위에 포함하지 않습니다.

```sh
dev --repo /path/to/repo "이 프로젝트에 검색 기능을 구현해줘"
dev --repo /path/to/repo --plan-only "서비스 기획을 정리해줘"
dev --repo /path/to/repo --routing rules "간단한 오탈자 수정"
dev --repo /path/to/repo --engine claude "설계를 검토해줘"
```

`--plan-only`는 Astra 판단만 실행하므로 **Codex 사용량을 소비**하며 `.ai`에 결과를 저장합니다.
`--dry-run`은 모델을 호출하거나 파일을 쓰지 않아 최종 엔진도 아직 결정하지 않습니다.
`--engine`을 지정하거나 `--routing rules`를 사용하면 Astra 판단 호출을 건너뜁니다.
판단은 최대 120초이며 읽기 전용 샌드박스에서 도구 사용 없이 분류하도록 요청합니다.
일반 CLI 설정과 Headroom은 유지합니다. 읽기 전용 모드는 별도의 완전한 격리 환경은 아닙니다.

Finder에서 `start.command`를 두 번 클릭하고 기존 Git 저장소의 전체 경로와 작업 내용을 입력합니다. 경로는 따옴표 없이 입력합니다.

```sh
./dev --doctor
./dev --repo /path/to/repo --dry-run "새 서비스 기획"
./dev --repo /path/to/repo "새 서비스 기획"
./dev --repo /path/to/repo "기획을 바탕으로 앱을 구현해줘"
```

기본 설치 경로는 `~/Documents/Codex/TernPilot`이며 `~/Documents/Codex/ai-router-bin/dev`와 `ternpilot`은 같은 실행 파일을 가리킵니다. 현재는 작업마다 실행하는 방식으로, 상시 On/Off 앱은 아닙니다.

## 분류

| 작업 | 기본 배정 |
| --- | --- |
| 구현·빌드·설계·디버깅·중요 리뷰·고영향 작업 | GPT-6 Astra 또는 Claude Opus 5.5 |
| 기획·아이디어·요약·일반 피드백 | Gemini 4 Argon (High) |
| 불명확한 요청 | Codex GPT-6 Astra |

두 주력 엔진은 동급이며 구현/설계 전용으로 나누지 않습니다. Astra가 작업에 맞춰 고르며,
확인하지 않은 성능·잔여 한도를 선택 근거로 만들지 않도록 지시합니다.
Astra의 판단·실행은 모두 `medium`, `service_tier=default`이며 Opus는 `--effort medium`과
자식 프로세스의 `CLAUDE_CODE_DISABLE_FAST_MODE=1`로 빠른 모드를 끕니다.
현재 Codex 앱 모델이나 전역 Headroom 설정을 변경하는 기능은 아닙니다.

위 표는 Astra에 전달하는 배정 기준입니다. Gemini가 비활성화되어 있거나 구현·중요 리뷰·고영향
작업으로 분류되면 강한 엔진으로 제한합니다. 모델 분류와 문자열 보완 규칙은 완벽하지 않으므로
중요한 작업은 `--plan-only`로 이유를 확인하거나 `--engine codex|claude`로 지정하세요.
`--routing rules`는 기존의 모델 호출 없는 문자열 분류를 사용합니다.

Astra 판단이 한도·rate-limit으로 실패하면 Claude로 인계합니다. 권한 거부, 시간 초과,
잘못된 JSON 결과는 작업 실행 전에 중단합니다. 판단 기록은 `.ai/HANDOFF.json`의 `planner`와
`history.jsonl`의 `phase: routing`에 남아 실제 작업 기록과 구분됩니다.

- Codex: `codex exec --model gpt-6-astra --config 'model_reasoning_effort="medium"' --config 'service_tier="default"' --json --sandbox workspace-write -`
- Claude: `CLAUDE_CODE_DISABLE_FAST_MODE=1 claude -p --model claude-opus-5-5 --effort medium --output-format json --permission-mode acceptEdits --no-session-persistence`
- Gemini: `agy --model gemini-4-argon-high --effort high --mode plan --sandbox --disable-slash-commands --input-format stream-json --output-format stream-json`

Gemini는 공식 Antigravity CLI (`agy`)를 통해 구독 계정으로 연결합니다.
모델은 `gemini-4-argon-high`, 추론 수준은 명시적으로 `high`입니다. 계획 모드와 터미널
샌드박스를 사용하고, 요청은 JSON으로 stdin에 전달합니다. `SUCCESS` 결과만 완료로 인정하며,
권한 거부와 취소는 다른 엔진으로 우회하지 않습니다.

2026-10-19 이 Mac에서 Argon High 직접 응답과 TernPilot 통합 호출을 모두 확인했습니다.
이전 Gemini CLI 0.46.0은 개인 계정에서 `UNSUPPORTED_CLIENT`를 반환했습니다.
`gemini_backend: legacy`는 구형 CLI 호환용으로만 남겨 두었습니다. 기본값은 `agy`입니다.
새 Mac에서는 `agy models`로 모델을 확인하고 실제 요청이 성공한 후 자동 배정을 켜세요.
로그인은 `agy`의 공식 대화형 절차를 이용합니다. API 키 과금으로 자동 전환하지 않습니다.

`--config`로 명시한 설정 파일이 우선하며, 생략하면 실행 파일 옆의 `config.json`을 읽고 없으면 기본값을 사용합니다.

Gemini CLI 이용 자격은 Gemini 웹 구독과 별개일 수 있습니다. 계정이 `IneligibleTierError`를 반환하면 `config.json`의 `gemini_enabled`를 `false`로 두고 로그인·CLI 자격을 먼저 해결하세요. 설치 프로그램은 Gemini를 기본 비활성화합니다.

Codex quota/rate-limit은 즉시 다른 미사용 강한 엔진으로 넘기고 일반 실패는 기본 두 번 시도합니다. Gemini 실패는 Claude, 필요하면 Codex로 승격합니다. 강한 모델의 작업을 Gemini로 낮추지 않습니다. 사용 가능한 강한 엔진을 소진하면 종료합니다. 권한/승인 거부는 다른 엔진으로 우회하지 않습니다.

## 상태·중단·기록

실행 시 저장소의 `.ai/`에 `HANDOFF.json`, `DECISIONS.md`, `history.jsonl`, 실행별 로그를 만듭니다. 기존 소유 표시가 없는 상태 파일과 심볼릭 링크는 거부하며 동일 저장소의 중복 실행을 잠급니다. reset/stash/commit은 자동 수행하지 않습니다. Gemini는 프로젝트 파일과 DECISIONS.md를 수정하지 않습니다.

명시적인 성공 결과를 확인해야 완료로 처리합니다. 논리적 정확성이나 모든 테스트 통과까지 자동 보장하지는 않습니다. Ctrl-C·SIGTERM·SIGHUP는 자식 프로세스 그룹을 종료하고 상태를 보존합니다. 강제 종료(SIGKILL)와 전원 차단은 정리 코드를 실행할 수 없습니다.

`DUORELAY_CHILD=1`과 기존 `.ai` 소유 표시는 이전 버전과 호환을 위해 유지합니다. 자식 작업의 중첩 Router 호출은 차단합니다. 로그에는 요청과 코드가 포함될 수 있으므로 프로젝트 정책에 맞게 보관하고 Git 추적을 제외하십시오. Claude의 세션 저장 끄기는 서버 보관까지 끄는 의미가 아니며 Gemini CLI에도 자체 세션 기록이 있습니다.

## Codex 새 대화 전역 위임

`codex-global.instructions.txt`는 새 Codex 대화에서 필요한 요청·코드·요약을 Anthropic 또는 Google의 CLI로 위임하도록 하는 설정입니다. 사용자 동의 아래 실제 전역 지침 경로를 확인하고 기존 내용을 백업·보존한 뒤 추가합니다. 프로젝트 제한, 사용자의 모델 선택, 기존 승인 규칙, Headroom은 유지합니다.

이 지침은 Codex가 도구를 사용할 수 있는 동안 작동합니다. 모든 입력을 첫 모델 호출 전에 가로채거나, 앱이 한도로 완전히 멈춘 뒤 자동 복구하는 기능은 아닙니다. 전체 프로세스 자동 전환은 `dev`/`ternpilot`로 시작한 작업에서 제공합니다. ChatGPT 웹/데스크톱, Claude/Gemini 웹앱, 다른 Mac에는 이 설정만으로 적용되지 않습니다.

## 다른 Mac mini에 설치

1. [GitHub Releases](https://github.com/Jakelul/TernPilot/releases)에서 macOS ZIP을 받아 새 Mac에서 압축을 풉니다.
2. Python 3.9 이상, Git, Codex CLI와 Claude Code를 준비하고 해당 Mac에서 각각 로그인합니다. Gemini는 사용 자격 확인 후에 연결합니다. 설치 프로그램은 로그인 정보·API 키·결제 방식을 복사하거나 변경하지 않습니다.
3. `Install TernPilot.command`를 두 번 클릭합니다. Codex의 새 대화에서도 위임하려면 안내를 읽고 `y`를 입력합니다. 작업에 필요한 요청·코드·요약이 선택된 제공자에게 전달됩니다.
4. 새 Codex 대화를 열거나 `~/Applications/TernPilot.app`을 실행합니다. 설치한 CLI와 옵션은 `~/Documents/Codex/TernPilot/dev --doctor`로 확인합니다.

터미널에서는 압축을 푼 폴더에서 다음 명령으로 같은 설치를 할 수 있습니다.

```sh
python3 install.py --enable-codex
export PATH="$HOME/Documents/Codex/ai-router-bin:$PATH"
dev --repo /path/to/repo --dry-run "앱을 구현해줘"
dev --repo /path/to/repo "앱을 구현해줘"
```

`--enable-codex`를 생략하면 명령과 앱만 설치합니다. PATH를 계속 사용하려면 위 `export` 한 줄을 `~/.zshrc`에 추가합니다. 설치 프로그램은 셸 설정을 바꾸지 않으며 기존 명령·설정은 보존합니다. 기존 설치와 전역 지침은 갱신 전에 백업합니다. 절대 경로는 해당 Mac에 맞게 자동 적용합니다. Gemini는 새 설치에서 기본 비활성화되며, 계정 자격 확인이 성공한 뒤 설치 폴더의 `config.json`에서 `gemini_enabled`를 `true`로 바꿀 수 있습니다.

macOS에서 다운로드한 실행 파일을 바로 열 수 없다면 압축을 푼 폴더에서 위 Python 설치 명령을 사용합니다. 이 프리뷰는 macOS용이며 Windows 실행은 아직 지원하지 않습니다.

## 클라우드 개발과 대화별 사용량

GitHub 저장소를 기준 소스로 사용합니다. ChatGPT cloud Work와 GitHub Codespaces의 개발 절차는 [CLOUD_START.md](CLOUD_START.md)에 있습니다. 코드를 내려받는 것만으로 유료 Codespace가 생성되거나 제공자 로그인이 이전되지는 않습니다.

ChatGPT의 `TernPilot 클라우드 개발` 작업은 소스 수정, 오프라인 테스트, 설치 ZIP 제작을 위한 작업환경입니다. 상시 가동 서버나 Mac 로그인 정보를 대신하는 서비스는 아닙니다. 클라우드에서 받은 새 ZIP도 각 Mac에 설치해야 하며 로컬 프로젝트와 `.ai` 상태는 자동 동기화되지 않습니다.

Codex에서 일반 대화와 위임 판단에는 Codex 사용량이 듭니다. 실제 위임한 작업만 Claude/Gemini 사용량을 쓰므로 모든 대화의 토큰이 자동으로 공유되거나 Codex 사용량이 0이 되는 구조는 아닙니다. 단순한 질문은 현재 대화에서 직접 답합니다. 외부 작업의 로컬 세션·로그와 각 제공자의 기록 정책은 별개이며, Claude/Gemini 웹앱에 같은 채팅이 자동 복제되는 기능도 없습니다.

## 검증

`python3 -m unittest test_router.py`로 20개 오프라인 통합 테스트를 실행합니다. 분류 우선순위, 세 엔진, quota/반복 실패, 권한 거부, 상태 보존, 설치 링크, 재귀 차단, 시간 초과와 중단 및 다른 사용자 경로에 재설치할 때의 설정 보존을 확인합니다. 확인한 CLI 버전은 Codex 0.160.0, Claude Code 2.1.285, Gemini CLI 0.46.0입니다. 실제 Astra와 Opus 응답은 성공했으며, Gemini 계정 거부 후 Claude로 전환되는 것도 확인했습니다.

공식 참고: [Gemini 모델 별칭](https://geminicli.com/docs/cli/cli-reference/), [Gemini 비대화형 실행](https://geminicli.com/docs/cli/headless/), [Codex 전역 지침](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## 라이선스

소스를 공개하되 모든 권리를 유보합니다. 오픈소스 라이선스가 부여된 프로젝트가 아니며 재사용·수정·재배포·상업적 이용에는 권리자의 별도 허가가 필요합니다. 자세한 조건은 [LICENSE](LICENSE)를 확인하세요.

## 로컬 대화 화면과 Obsidian

`chat-config.example.json`을 설치 폴더의 `chat-config.json`으로 복사해 로컬 프로젝트 경로, 대화 데이터 폴더, Obsidian 볼트 경로를 지정한 뒤 `dev --chat`을 실행합니다. 출력되는 인증 URL을 브라우저에서 엽니다. localhost에서만 실행되며 프로젝트 선택, 대화 목록, 자동/수동 엔진 선택을 지원합니다. 중지는 실행 터미널에서 Control+C입니다.

대화의 최근 4개 턴(최대 24,000자)을 다음 요청에 전달합니다. 원본 전체 세션 복구는 아닙니다. 요청 시점과 완료 시점에 `TernPilot/대화/<ID>.md`를 저장합니다. 동기화된 Markdown은 Obsidian에서 읽으며 다른 Mac의 UI로 자동 가져오는 기능은 아직 없습니다. 한 서버에서 작업은 한 번에 하나입니다.

GitHub에는 소스·테스트·설정 예시를, Obsidian에는 요청·결과·업무 기록을 보관합니다. 실제 경로가 포함된 chat-config.json, 로그인 정보, 대화와 원시 로그는 공개 저장소에서 제외합니다. 이 UI는 사용자가 입력한 내용과 모델의 최종 답변을 저장하므로 민감한 정보를 입력하지 마세요.

Gemini 계획 작업에는 README 일부와 Git 상태를 미리 전달해 쉘 명령을 요청할 필요를 줄입니다. 추가 자료가 필요한 경우 부족한 점을 설명하도록 요청합니다. CLI가 denied_actions를 반환하면 차단 상태를 기록하고 다른 엔진으로 우회하지 않습니다. 권한을 모두 해제하지 마세요.

Obsidian Sync의 원격 볼트 연결은 각 Mac에서 별도로 설정합니다. 코드와 인증, 실행 중인 프로세스는 Sync로 전달되지 않습니다. 현재 버전은 개발용 미리보기입니다.

### 모델별 사용량 패널
상단의 Codex·Claude Code·Argon 수치는 이 Mac에 등록한 프로젝트의 `.ai/router-runs` 로그에서 읽은 누적 입력·출력 토큰입니다. Astra 분류와 실패한 호출도 사용량을 보고한 경우 포함합니다. 구독 잔여량, 계정 전체 사용량, 비용 추정치가 아닙니다. 캐시는 제공자가 보고한 별도 필드이며 입력 토큰에 포함되는 방식이 다르므로 단순 합산하지 않습니다. 기록이 없으면 0이 아닌 “보고된 사용량 없음”을 표시합니다. 라우터 밖에서 실행한 CLI나 직접 리뷰는 이 합계에 포함되지 않습니다.

왼쪽은 프로젝트 폴더와 선택한 프로젝트의 대화 목록으로 구분합니다. 프로젝트 등록은 chat-config.json의 projects에서 관리합니다.

상단은 모델명과 잔여 한도 표시만 제공합니다. Codex와 Claude는 현재 로그인 계정의 7일 한도를 60초마다 조회합니다. Argon은 기존 Token Cat 상태표시줄 연동의 명시적인 Argon/Gemini 주간 버킷을 읽어 표시합니다. 토큰 사용 비중으로 대체하지 않습니다. 사용 기록은 기본으로 접혀 있으며 제목 오른쪽 삼각형으로 펼칩니다. 아래 사용 기록을 누르면 실행 ID, 프로젝트, 요청, 상태, 분류/작업별 토큰과 캐시 보고값을 볼 수 있습니다. 새 실행은 `.ai/router-runs/<실행ID>/task.json`에 요청 연결 정보를 보존합니다. 이전 로그에서 요청을 복원할 수 없으면 “요청 기록 없음”으로 표시합니다. 새 웹 대화의 Markdown에도 실행 ID와 사용 내역을 함께 기록합니다. 크레딧은 제공자가 보고하지 않아 추정하지 않습니다.


주간 조회는 Codex app-server의 account/rateLimits/read와 Claude의 OAuth usage 응답을 사용합니다. Claude는 기존 macOS Keychain CLI 자격 증명을 요청 시에만 읽으며 앱이나 볼트에 저장하지 않습니다. Claude 조회 엔드포인트는 변경될 수 있습니다. 숫자에 마우스를 올리면 조회 시각과 초기화 시각을 볼 수 있습니다. 서버가 실행 중일 때 60초마다 갱신하며, 조회 실패 시 미확인으로 바꾸고 재시도 간격을 최소 5분으로 늘립니다. 구독 한도 조회 자체는 모델 생성을 실행하지 않습니다. Argon의 일간/모델별 한도를 주간으로 환산하지 않습니다.


### Argon 주간 자동 갱신
이 Mac에서는 사용자의 명시적 승인으로 chat-config.json의 argon_quota_repo를 TernPilot 경로로 지정했습니다. 서버는 별도 Antigravity CLI 세션에서 내장 /usage만 실행하고 종료한 뒤 60초 간격으로 다시 조회합니다. CLI 시작/조회 시간만큼 실제 간격은 늘어날 수 있습니다. 일반 모델 프롬프트는 전송하지 않습니다. 초기 프롬프트 옵션(-i /usage)은 모델 요청으로 해석될 수 있어 사용하지 않습니다.

기존 Token Cat의 `~/Library/Application Support/TokenCat/antigravity-quota.json` 연동이 필요합니다. 상태표시줄 설정을 덮어쓰지 않습니다. `gemini-argon-wtu-weekly`와 `gemini-weekly` 중 낮은 잔여율을 표시하며 5시간/타사 모델 버킷은 제외합니다. 120초 이상 오래된 관측값, 로그아웃, 만료되거나 잘못된 값은 미확인으로 처리합니다. 다른 Mac에서는 이 연동과 프로젝트 신뢰 승인을 별도로 준비해야 합니다. 설정을 생략하면 기존 관측 파일만 읽으며 자동으로 CLI를 시작하지 않습니다.


## 두 Mac에서 자동 업데이트

GitHub `main`이 배포 기준입니다. 소스 변경은 검토·테스트 후 GitHub에 커밋/푸시해야 합니다. 로컬 편집을 무조건 자동 업로드하지는 않습니다.
각 Mac에서 최신 소스를 설치한 뒤 아래 명령을 한 번 실행합니다.

```sh
python3 ~/Documents/Codex/TernPilot/update.py --enable
```

macOS 로그인 시와 매시간 GitHub를 확인합니다. 새 커밋을 임시 폴더에 받고 테스트가 통과한 경우에만 설치 파일을 갱신합니다. `dev` 또는 채팅 UI가 실행 중이면 갱신을 미루므로 UI를 닫으면 다음 확인 때 적용됩니다. `router.py`/`chat.py`를 직접 실행하지 말고 `dev`를 사용하세요. 설치 코드에 로컬 수정이 있으면 중단합니다.

- 인증, `config.json`, `chat-config.json`, 대화 기록, Obsidian 볼트, Headroom 설정은 업데이트 대상이 아닙니다.
- 적용 실패 시 기존 코드를 복구합니다. 중간에 프로세스가 강제 종료되면 다음 업데이트가 복구하며, 복구 전에는 `dev` 실행을 막습니다.
- 기록: `~/Documents/Codex/TernPilot/update.log`
- 즉시 확인: `python3 ~/Documents/Codex/TernPilot/update.py`
- 중지: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.ternpilot.update.plist` 후 해당 plist를 삭제합니다.

컴퓨터가 꺼져 있거나 로그아웃 상태이면 업데이트는 다음 로그인 후 진행됩니다. GitHub 코드는 동기화되지만 각 Mac의 로그인과 Obsidian Sync 연결은 별도입니다. Argon 주간 게이지는 각 기기의 Token Cat Antigravity statusLine 어댑터가 필요합니다.

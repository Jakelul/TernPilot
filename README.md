# TernPilot · 턴파일럿

**한 번의 요청, 가장 적합한 AI.**

Local AI task routing across Codex, Claude Code and Gemini CLI.
**v0.3.0-preview.1 · macOS preview · Source available, all rights reserved.**

[Download the macOS preview](https://github.com/Jakelul/TernPilot/releases) · [Cloud development](CLOUD_START.md) · [Changelog](CHANGELOG.md) · [License](LICENSE)

TernPilot은 기존 Codex·Claude Code·Gemini CLI로 작업을 나누고 각 계정의 사용량을 사용합니다. 현재 Mac용 프리뷰이며 Python 표준 라이브러리만 사용합니다. 세 서비스의 토큰 잔액을 합치는 기능은 아닙니다.

## 사용

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
| 보안·인증·결제·마이그레이션·중요한 설계/리뷰 | Claude Opus 5.5 |
| 본격적인 구현·빌드·개발·수정 | Codex GPT-6 Astra |
| 기획·아이디어·요약·일반 피드백 | Gemini Pro |
| 불명확한 요청 | Codex GPT-6 Astra |

영향이 큰 작업은 기획/요약 키워드가 있어도 강한 모델로 보냅니다. 보수적인 문자열 규칙이며 분류 자체에 모델을 호출하지 않습니다. `--engine codex|claude|gemini`로 명시적으로 선택할 수 있습니다.

- Codex: `codex exec --model gpt-6-astra --json --sandbox workspace-write -`
- Claude: `claude -p --model claude-opus-5-5 --output-format json --permission-mode acceptEdits --no-session-persistence`
- Gemini: `gemini --model pro --output-format json --approval-mode plan --prompt ...`

Gemini는 읽기 전용 기획/분석 모드입니다. `pro`는 공식 CLI 별칭으로 실제 모델은 계정과 CLI 설정에 따라 선택됩니다. Pro 접근 오류를 Flash 선택이나 API 키 과금으로 감추지 않습니다. `--config`로 명시한 설정 파일이 우선하며, 생략하면 실행 파일 옆의 `config.json`을 읽고 없으면 기본값을 사용합니다.

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

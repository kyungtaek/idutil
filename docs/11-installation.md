# 설치 및 초기 설정

idutil 0.2.0은 GitHub Releases의 macOS 실행 파일로 설치한다. Python·uv·소스 빌드가 필요 없다. Apple Silicon과 Intel을 지원하며 배포 기준은 macOS 15 이상이다.

## 1. 바이너리 설치

터미널에서 최신 릴리즈의 설치 스크립트를 다운로드해 실행한다.

```sh
installer="$(mktemp)"
curl --proto '=https' --tlsv1.2 -fsSL \
  https://github.com/kyungtaek/idutil/releases/latest/download/install.sh \
  -o "$installer" && sh "$installer"
rm -f "$installer"
```

스크립트는 현재 Mac의 아키텍처를 선택하고 같은 릴리즈의 실행 파일과 SHA256SUMS를 받는다. 체크섬·압축 파일 구성·실행 파일 버전을 확인한 뒤 `~/.local/bin/idutil`에 설치한다. 기존 실행 파일은 검증이 끝난 뒤 교체한다. 셸 설정이나 Worktrunk를 자동으로 바꾸지는 않는다.

PATH에 없다면 현재 터미널에 적용하고, 새 터미널에서도 유지하도록 같은 줄을 `~/.zshrc`에 한 번 추가한다.

```sh
export PATH="$HOME/.local/bin:$PATH"
idutil --version
```

특정 버전이나 다른 경로를 선택할 수 있다.

```sh
installer="$(mktemp)"
curl --proto '=https' --tlsv1.2 -fsSL \
  https://github.com/kyungtaek/idutil/releases/download/v0.2.0/install.sh \
  -o "$installer" && sh "$installer" --version v0.2.0 --bin-dir "$HOME/.local/bin"
rm -f "$installer"
```

기존 uv 설치를 사용 중이면 먼저 `uv tool uninstall ios-disk-util`로 해당 CLI를 해제한 뒤 바이너리를 설치한다. 등록 정보·캐시는 유지된다. 설치 스크립트는 패키지 관리자가 만든 symlink를 임의로 덮어쓰지 않는다.

수동으로 설치할 경우 [릴리즈](https://github.com/kyungtaek/idutil/releases/latest)에서 `idutil-v<버전>-macos-arm64.tar.gz` 또는 `macos-x86_64.tar.gz`와 SHA256SUMS를 받는다. 체크섬을 확인하고 압축 안의 `idutil`을 PATH의 디렉터리에 배치한다. 실행 파일은 ad-hoc 서명이며 Developer ID 서명/공증은 하지 않았다. macOS가 실행을 차단하면 시스템 설정의 개인정보 보호 및 보안에서 출처를 확인한다. 설치 스크립트는 Gatekeeper 설정을 변경하지 않는다.

## 2. Worktrunk와 Xcode 준비

Worktrunk는 별도 설치한다. [공식 안내](https://worktrunk.dev/#install)를 기준으로 Homebrew를 사용하는 경우:

```sh
brew install worktrunk
wt config shell install zsh --yes
```

새 터미널을 열고 `wt --version`을 확인한다. 셸 연동은 `wt switch` 후 디렉터리를 전환하는 기능이다. zsh 자동완성이 필요하면 `.zshrc`의 wt 초기화 줄 앞에 `autoload -Uz compinit && compinit`을 둔다.

기존 Xcode 설치를 사용한다. 경로를 확인하려면:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -version
```

시스템 전체 xcode-select를 바꿀 필요는 없다. Tuist·XcodeGen은 idutil의 요구사항이 아니다.

## 3. 적용할 iOS Git 저장소 등록

앱 소스 저장소로 이동한 뒤 실제 project/workspace와 scheme을 지정한다. Worktrunk로 브랜치를 만들려면 최소 한 개의 커밋이 필요하다.

```sh
idutil setup --ios \
  --project App.xcworkspace --scheme App \
  --developer-dir /Applications/Xcode.app/Contents/Developer \
  --agent all --dry-run

idutil setup --ios \
  --project App.xcworkspace --scheme App \
  --developer-dir /Applications/Xcode.app/Contents/Developer \
  --agent all
```

`--project`는 저장소 루트 기준 상대 경로이며 `.xcodeproj`도 가능하다. local SPM·development Pod·vendored framework 입력은 `--input Packages --input Vendor`처럼 실제 경로를 추가한다.

`--agent all`은 Codex/Claude 프로젝트 규칙을 설치한다. `codex`, `claude`, `none`도 선택할 수 있다. setup은 에이전트나 MCP 서버 자체를 설치하지 않는다.

| 위치 | 내용 |
| --- | --- |
| `~/.config/worktrunk/config.toml` | pre-start / pre-remove / post-remove hook |
| `~/Library/Application Support/idutil` | 등록 정보, 독립 실행 파일 snapshot, 작업별 데이터, GC 계획 |
| 앱 저장소 `.agents/skills/ios-disk-util` | Codex 규칙 |
| 앱 저장소 `.claude/skills/ios-disk-util` | Claude 규칙 |
| 앱 저장소 AGENTS.md / CLAUDE.md | 기존 내용을 보존하는 관리 블록 |

등록된 저장소에만 적용한다. 기존 hook을 보존하며, standalone 배포의 hook은 Python 환경이나 원래 다운로드 경로에 의존하지 않는다.

## 4. 첫 작업과 업데이트

```sh
wt switch --create feature-example --base main
idutil prepare --json
idutil doctor --integration
idutil status --measure
```

prepare의 buildContext를 기존 xcodebuild/MCP에 적용한다. 폴더 생성만으로 설정 적용을 보증하지 않는다. XcodeBuildMCP 2.7.0 테스트는 기존 xcodebuild test로 진행한다. [사용자 매뉴얼](06-user-manual.md)

업데이트는 설치 스크립트를 다시 실행한 뒤, 등록한 각 앱 저장소에서 다음 명령으로 hook snapshot도 갱신한다.

```sh
idutil setup --update --dry-run
idutil setup --update
```

연결 해제는 각 등록 저장소에서 `idutil setup --uninstall`을 실행한다. 모든 연결을 해제한 뒤 `~/.local/bin/idutil` 실행 파일을 제거할 수 있다. 관리 데이터와 사용자 편집 규칙은 자동으로 삭제하지 않는다.

## 5. 소스 개발과 E2E

바이너리 사용자는 이 절차가 필요 없다. 소스 수정 시 `uv sync`와 `uv run python -m unittest discover -s tests -v`를 사용한다. 바이너리 빌드는 [배포 절차](13-release-process.md)를 참고한다.

설치된 CLI의 실제 Xcode E2E는 XcodeGen과 예제 CocoaPods bundle까지 준비한 뒤 실행한다. [샘플 준비](../examples/mixed-dependencies/README.md)

```sh
python3 scripts/e2e_installed.py --install-user-hooks
```

이 명령은 실제 사용자 hook에 전용 샘플을 등록한다. Xcode 종료 후 테스트 자원의 GC까지 확인하며, 실제 Claude/Codex 자율 세션과 iOS 시뮬레이터 실행은 포함하지 않는다. [기존 설치 E2E 결과](12-installed-e2e-results.md)

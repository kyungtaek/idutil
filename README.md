# iOS Disk Util (`idutil`)

Worktrunk와 함께 쓰는 **DerivedData 격리·캐시 연결·GC CLI**. 현재 버전은 **0.2.0**이며 [GitHub Releases](https://github.com/kyungtaek/idutil/releases/latest)에서 바이너리를 제공한다.

Worktrunk가 워크트리를 관리하고, idutil은 작업별 빌드 데이터를 관리한다. 빌드·테스트·실행은 기존 Xcode 도구가 수행한다. Tuist는 필수가 아니다.

[설치 안내](docs/11-installation.md): 릴리즈 바이너리 다운로드, PATH, 프로젝트 등록, 업데이트·해제, 실제 설치 E2E 절차.

[로컬 설치 E2E 결과](docs/12-installed-e2e-results.md): 실제 사용자 hook으로 생성·빌드·복구·제거·GC 삭제 통과, 회귀 테스트 24개 통과.

```sh
installer="$(mktemp)"
curl --proto '=https' --tlsv1.2 -fsSL \
  https://github.com/kyungtaek/idutil/releases/latest/download/install.sh \
  -o "$installer" && sh "$installer"
rm -f "$installer"
export PATH="$HOME/.local/bin:$PATH"

brew install worktrunk
wt config shell install zsh --yes
# 새 터미널을 열고 iOS 앱 저장소에서 실행
idutil setup --ios --project App.xcworkspace --scheme App --agent all --dry-run
idutil setup --ios --project App.xcworkspace --scheme App --agent all
wt switch --create feature-login --base main
idutil prepare --json
idutil status --measure
idutil doctor --integration
idutil gc
```

macOS 15 이상, Apple Silicon/Intel용 독립 실행 파일을 제공한다. Python이나 uv 설치는 필요 없다. 설치 스크립트는 아키텍처 선택과 SHA-256 검증 후 `~/.local/bin/idutil`에 설치한다. Worktrunk는 별도로 설치한다. 기존 uv 설치가 있으면 `uv tool uninstall ios-disk-util` 후 바이너리 설치로 전환한다.

`setup`은 실제 iOS Git 저장소 안에서 실행하고 project/scheme을 바꾼다. Worktrunk에는 최소 한 개의 커밋이 필요하다. 이미 등록한 저장소는 업데이트 후 `idutil setup --update`로 hook도 갱신한다.

- `setup`: 등록된 저장소에만 적용되는 Worktrunk hook과 Codex/Claude 규칙을 설치한다.
- `prepare`: 작업별 경로를 재사용하거나 손상된 binding을 새 관리 경로로 복구한다. JSON의 `buildContext`를 기존 빌드 도구에 적용한다.
- `status`: 소유 작업·세대·상태·선택적 용량을 보여준다.
- `doctor`: 설정 충돌을 진단한다. 실제 MCP 상태는 사용자가 제공한 설정 스냅샷이 있어야 비교할 수 있다.
- `gc`: 삭제 계획을 만든다. 적용은 빌드 도구를 종료한 상태에서 `gc --apply <planId> --quiescent`로 명시한다.

기본은 private cache다. `--shared-cache`는 검증한 Xcode 27.0의 native compilation cache 연결만 활성화한다. 자체 바이너리 캐시나 DerivedData 디렉터리를 여러 작업에 공유하지 않는다. native CAS 내부와 외부 SPM/CocoaPods 캐시는 GC하지 않는다.

**격리의 한계:** hook은 경로를 준비하고 에이전트 규칙은 그 경로 적용을 지시한다. 임의의 빌드 호출을 OS 수준에서 차단하는 기능은 없다. XcodeBuildMCP 2.7.0의 `build_macos`는 지정 경로를 사용했지만 `test_macos`의 후반 단계는 설정을 누락했다. 해당 버전은 기존 `xcodebuild test`를 사용한다. Xcode GUI, 실제 Claude/Codex 호스트 세션, iOS 시뮬레이터 실행은 아직 검증하지 않았다.

[사용자 매뉴얼](docs/06-user-manual.md) · [구현/검증 결과](docs/10-implementation-status.md) · [설치 상세](docs/07-agent-integration.md) · [개발 계획](docs/05-development-plan.md) · [캐시 정합성 설계](docs/03-mixed-dependency-consistency.md) · [전체 검증 계획](docs/08-post-implementation-checklist.md)

개발 환경의 검증:

```sh
uv sync
uv run python -m unittest discover -s tests -v
```

실제 Worktrunk 통합 테스트는 `.tools/worktrunk/wt` 또는 `IDUTIL_TEST_WT`로 지정한 실제 바이너리가 필요하며 없으면 skip된다. 추가 Xcode 실험은 `scripts/verify_xcode.py`의 선행 조건을 확인한 뒤 명시적으로 실행한다. 이 스크립트는 빌드와 테스트 데이터를 생성한다.

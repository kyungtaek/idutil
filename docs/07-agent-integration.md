# 에이전트 설치 — Worktrunk 사용 안내와 iOS 캐시 hook

갱신일: 2026-11-02. 설치 스크립트·skill·Worktrunk hook을 구현하고 임시 저장소에서 검증했다. 후속 [로컬 설치 E2E](12-installed-e2e-results.md)에서는 사용자 Worktrunk hook과 샘플의 프로젝트 규칙까지 실제 설치했다. 전역 에이전트 skill 설치는 하지 않았다. 아래 상세 설계 중 호스트 자동 감지·로드 검증 등 미구현 항목은 [구현 결과](10-implementation-status.md)를 참고한다. 현재 사용법은 [사용자 매뉴얼](06-user-manual.md)이 우선한다.

## 1. 설치 후 역할

| 요청 | 경로 |
| --- | --- |
| 워크트리 생성·조회·참조·전환·제거 | 에이전트 → Worktrunk |
| iOS 워크트리 캐시 준비/제거 전 점검/참조 해제 | Worktrunk hook → 우리 도구 |
| 빌드·실행·테스트 | 기존 Xcode·에이전트 도구 |
| 캐시 용량·오류·잔여물 확인 | 우리 status/doctor/gc |

워크트리 활동 추적과 에이전트별 생성/제거 어댑터는 Worktrunk의 기존 기능을 우선 이용한다. 우리 패키지는 같은 이벤트의 대체 hook을 별도로 설치하지 않는다.

## 2. 설치 인터페이스

구현된 공개 명령은 아래와 같다. 최초 setup에는 `--ios --project <상대 경로> --scheme <이름>`이 추가로 필요하다.

```text
scripts/install-agent.sh --agent all --scope project --dry-run
scripts/install-agent.sh --agent claude --scope project
scripts/install-agent.sh --agent all --update --dry-run
scripts/install-agent.sh --agent all --uninstall --dry-run
idutil setup --agent all --dry-run
idutil prepare --worktree <path> --json
idutil prepare --worktree <path> --fresh --json
idutil doctor --integration --json
```

설치 스크립트는 setup을 호출하는 얇은 진입점이다. 설치 로직을 두 벌 만들지 않는다. Worktrunk/우리 CLI의 존재·버전·지원 호스트를 검사하고 없으면 선행 조건을 안내한다. 도구를 몰래 내려받거나 Git fallback을 구성하지 않는다. prepare는 생성 hook·에이전트·사람이 함께 사용하는 공개 준비/복구 명령이다.

Worktrunk 플러그인이 이미 있으면 재사용한다. 없으면 공식 설치 방법과 필요한 변경을 계획에 포함하고 사용자가 선택한 설치 범위에서만 연결한다. 현재 설치기는 Worktrunk 플러그인을 자동 설치하거나 호스트 로드를 검증하지 않는다.

## 3. 무엇을 설치하는가

| 구성 | 설치 내용 |
| --- | --- |
| 공통 안내 skill | iOS 대상의 워크트리는 wt로 생성/조회/참조/제거, 빌드는 기존 도구 사용, 캐시는 status/doctor/gc로 확인 |
| Worktrunk 에이전트 연결 | 기존 공식 플러그인/설정을 감지·재사용. 호스트별 지원 차이 진단 |
| Worktrunk cache hooks | pre-start → prepare, pre-remove → check-release, post-remove → release |
| 프로젝트 정책 | iOS 적용 경로, 관리 자원 위치, 캐시 준비/GC 보존 정책 |
| 빌드 경로 전달 | 생성 hook의 영속 binding, xcodebuild/XcodeBuildMCP 경로 설정 절차, 빌드 전 현재 워크트리 대조 규칙 |
| 짧은 규칙 블록 | 필요한 AGENTS.md/CLAUDE.md 안내만 추가. 기존 지침 보존 |
| 설치 manifest | 도구 소유 파일/설정 키·버전·hash·백업·적용 범위 |

우리 skill은 Worktrunk 명령 전체를 다시 설명하는 복제품이 아니다. iOS 적용 조건과 캐시 연동 운영 규칙만 보완한다. Worktrunk의 생성·제거 hook/marker를 덮어쓰거나 두 관리자가 동일 워크트리를 생성하지 않게 한다.

Codex의 .agents/skills와 Claude의 .claude/skills 등 공식 설치 경로를 사용하되 호스트·버전별 로드를 검사한다. [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Claude skills](https://code.claude.com/docs/en/skills)

## 4. iOS 조건 판정

- 프로젝트의 명시적 활성화/제외와 대상 경로가 우선이다. 현재 구현은 관리 루트의 registry에 setup 옵션을 기록한다.
- iOS target/SDK/platform 선언은 등록 추천 근거다. .xcodeproj/Podfile/Package.swift 존재만으로 판정하지 않는다.
- 모노레포는 iOS 작업 범위를 지정한다. 같은 저장소의 비대상 작업까지 캐시 정책을 강제하지 않는다.
- 비대상에서는 우리 캐시 hook이 아무 변경 없이 종료하고 Worktrunk의 기본 동작을 유지한다. 기본 생성을 대체하는 자체 WorktreeCreate hook이 아니므로 빈 경로 반환 문제가 생기지 않는다.
- 불명확한 대상은 활성화 필요로 안내한다. 파일을 자동 이동·삭제하지 않는다.
- 사용자 범위 설치도 등록된 프로젝트에만 적용한다.

조건 판정은 모든 cache hook과 setup/doctor에서 공통으로 사용한다. hook 진입 시 현재 디렉터리뿐 아니라 명시적 대상 워크트리·저장소를 기준으로 판단한다.

## 5. Worktrunk hook의 순서와 실패

pre-start 준비는 완료 후 다음 작업이 진행돼야 한다. 기존 병렬 hook이 먼저 빌드를 시작하지 않게 구성한다. pre-remove에서는 보존/사용 검사와 pending 기록만 하고, post-remove에서 실제 제거를 확인한 뒤 참조 해제/정리를 수행한다. post-remove의 cwd를 대상 경로로 사용하지 않는다. [공식 hook 동작](https://worktrunk.dev/hook/)

우리 pre-remove 성공 뒤 다른 hook이나 Git 제거가 실패해도 캐시는 유지한다. Worktrunk의 background 제거와 hook 실패·중복·지연을 고려하며, GC는 실패 기록과 실제 상태를 대조한다. [제거 동작](https://worktrunk.dev/remove/)

hook은 미승인 또는 --no-hooks 등으로 실행되지 않을 수 있다. 설치됨·신뢰/승인 필요·활성·마지막 준비 완료를 구분한다. 전역 승인 우회 옵션을 자동 설정하지 않는다. 설치 파일이 존재한다는 이유로 통합이 정상이라고 하지 않는다.

### 생성 hook → 에이전트 → 기존 빌드 도구

이 전달 과정은 첫 버전의 필수 기능이다. 폴더를 만들고 경로를 출력하는 것으로 끝내지 않는다.

1. prepare가 워크트리·프로필별 전용 DerivedData를 할당하고 관리 루트에 build context를 원자적으로 저장한다. schema version, repository/worktree ID, 재생성 세대, profile, project/workspace 경로, derivedDataPath 절대 경로를 포함한다.
2. hook 결과는 binding 식별자와 준비 상태를 알린다. 에이전트는 현재 대상 경로에서 `idutil status --json`으로 context를 읽는다. hook의 export는 상위 에이전트나 이미 실행 중인 MCP 설정을 바꾼다는 계약이 아니다.
3. 설치한 skill/규칙에 따라 에이전트가 사용하는 도구에 아래 설정을 적용한다. 실행 중인 MCP에 대한 설정은 그 도구의 세션 API로 전달한다. hook이 임의 에이전트 세션을 직접 제어한다고 가정하지 않는다.
4. 생성·전환·재개·MCP 재시작 후 다시 읽고 설정한다. 각 빌드/테스트 호출 전에도 binding과 유효 인수를 비교한다. 설정 파일 갱신만으로 기존 세션이 자동 reload됐다고 간주하지 않는다.
5. 정상 binding이 있고 호출 인수/세션 설정만 누락·과거 값이면 현재 binding을 재적용한다. binding 자체 누락·손상/실제 자원 충돌/확인된 캐시 오류는 prepare --fresh로 새 관리 경로에 복구한다. 이후 기존 도구로 빌드하며 기본 공용 위치로 fallback하지 않는다. 새 경로 생성·등록·적용도 불가능하면 구체적 error를 알린다.

복구 요청은 같은 공개 prepare 로직을 사용한다. skill은 셸의 임의 mkdir이나 비관리 임시 폴더로 우회하도록 안내하지 않는다. 정상 생성과 동일한 관리 루트·resource ID·프로필·소유권·참조·journal·GC 정책을 적용한다. 자동 복구는 관측한 이전 resource ID와 오류를 근거로 중복 요청을 합치고 1회만 시도한다. 새 binding 게시 후 기존 도구의 설정을 갱신한다. 이후 그 경로를 유지하고 이전 자원은 사용 종료·보존 조건 확인 후 GC한다. [복구 상세](05-development-plan.md#53-캐시-오류는-관리되는-새-공간으로-자동-복구)

hook이 binding을 게시한 prepared는 정상 인계 지점이다. 에이전트가 그 뒤 도구 설정을 확인한다. pre-start는 신규 생성 때만 실행되므로 기존 작업 전환/재개도 에이전트 규칙에서 확인한다. hook 종료 코드만으로 도구 설정이나 실제 사용까지 검증됐다고 표시하지 않는다.

idutil은 외부 빌드를 감시하거나 자동 재실행하지 않는다. 에이전트가 경로 대조나 외부 도구의 구체적 캐시 오류를 근거로 복구를 요청한다. 새 경로에서 test-without-building/앱 실행만으로 계속할 수 없는 경우 기존 도구의 build-for-testing/빌드를 먼저 수행한다. 오류 원인이 소스·서명·테스트일 때는 이 복구를 반복하지 않는다.

| 기존 도구 | 적용 및 확인 계약 |
| --- | --- |
| xcodebuild | build/test/build-for-testing/test-without-building 등 지원 작업에 `-derivedDataPath <binding의 절대 경로>`를 명시. 프로젝트/workspace도 같은 워크트리인지 확인. 기존 build 명령을 우리 CLI로 대체하지 않음 |
| XcodeBuildMCP | 설치 버전의 schema를 확인한 뒤 `session_set_defaults`의 `derivedDataPath`와 대상 project/workspace를 설정하고 `session_show_defaults`로 재확인. 호출별 override와 extraArgs도 대조. 지원되는 호출별 명시 인수가 있으면 같은 binding을 사용 |
| Xcode GUI | 별도 workspace/사용자 설정 어댑터 검증 대상. MCP/CLI 설정으로 GUI까지 바뀌었다고 간주하지 않음 |

XcodeBuildMCP의 현재 upstream 문서는 MobileBuildMCP 명칭을 사용한다. 이 계획은 사용자 요청의 XcodeBuildMCP 명칭을 유지하며 설치된 패키지·버전별 지원을 고정한다. 공식 문서는 `derivedDataPath` 세션 기본값과 set/show/clear 흐름, runtime 설정의 우선순위를 설명한다. 실제 버전의 설정 파일 이름·schema·도구별 적용 범위는 구현 시 확인한다. 최신 명칭으로 자동 교체하거나 업그레이드하지 않는다. [세션 기본값](https://github.com/getsentry/xcodebuildmcp.com/blob/main/app/docs/_content/session-defaults.mdx), [설정 우선순위](https://github.com/getsentry/xcodebuildmcp.com/blob/main/app/docs/_content/configuration.mdx)

하나의 MCP 서버에서 A/B의 mutable 기본값을 동시에 바꾸면 경로가 섞일 수 있다. 독립 세션을 사용하거나, 호출별 명시 경로의 독립성을 검증한 구성을 지원한다. 설정 후 조회 한 번만으로 동시성까지 보장하지 않는다. 기존 projectPath/workspacePath 중 반대쪽 값이 남는 경우도 갱신 시 정리·검증한다.

binding은 워크트리 외부의 관리 상태가 원본이다. 기기별 절대 경로를 추적 설정 파일에 커밋하지 않는다. 로컬 설정/포인터가 필요한 어댑터는 도구 소유 파일과 제외 규칙만 설치하며, 다른 워크트리의 binding을 복사하지 않는다. 실행별 경로는 현재 유효 binding으로 교정하고, 실제 자원 문제라면 새 세대로 복구한다. 사용자의 추적 설정/전역 설정을 임의 덮어쓰지는 않는다.

status/doctor는 준비 결과, 캐시 모드, 도구별 마지막 증거를 구분한다. 현재 MCP 설정은 에이전트가 직접 조회한다. idutil은 조회 가능한 설정/로그 등 근거와 binding 세대·관측 시각이 있을 때만 확인됨을 표시하고, 없으면 unknown으로 둔다. 상시 세션 감시/수집 서버를 추가하지 않는다. [구현 후 필수 체크리스트](08-post-implementation-checklist.md)

## 6. 에이전트별 범위

Worktrunk 공식 문서는 에이전트에 따라 활동 추적과 내장 워크트리 위임 지원이 다르다고 설명한다. 지원되는 Claude 연결은 기존 Worktrunk 플러그인을 이용한다. Codex 등에서는 지원되는 명시적 wt 호출 흐름을 안내하고, 모든 desktop/내장 managed-worktree 경로를 대체했다고 주장하지 않는다. [Worktrunk agent integration](https://worktrunk.dev/claude-code/)

우리 설치기는 Codex/Claude의 범용 PreToolUse를 추가해 빌드나 모든 파일 접근을 감시하지 않는다. 별도 강제 계층을 첫 버전에 넣지 않는다. skill은 안내이며 임의 셸·다른 도구의 우회 전체를 막는 보안 경계가 아니다.

지원 흐름은 **설정 누락은 재적용, binding/자원 오류는 새 관리 DerivedData로 복구 후 빌드 진행**을 필수로 검증한다. 복구 불가능한 실제 파일시스템/도구 설정 오류만 별도로 보고한다. ‘규칙을 설치했다’와 ‘모든 우회를 기술적으로 차단했다’를 구분한다. 해당 호스트에서 규칙 전달·설정 적용이 재현되지 않으면 그 조합을 미지원으로 표시한다.

기존 에이전트 호스트가 워크트리 소유자로 남는 경로에서는 이중 제거를 피하고 통합 제한을 표시한다. Worktrunk를 경유하는 프로젝트 흐름이 첫 지원 대상이다.

이전 조사에서 로컬 Codex CLI 0.155.0의 version/help만 확인했고 Claude 실행 파일은 당시 PATH에서 찾지 못했다. 실제 설치/활성화 시험은 아직 수행하지 않았다.

## 7. 설치·갱신·제거 품질

1. dry-run은 파일/설정 변경과 적용 프로젝트, 기존 플러그인·hook 충돌을 보여준다.
2. 기존 .config/wt.toml 및 에이전트 설정은 구조적으로 병합하고 도구 소유 키만 관리한다. 사용자 hook과 정책을 통째로 덮어쓰지 않는다.
3. 등록된 순서/스키마와 다른 hook의 의존성이 불명확하면 conflict로 남기며 동시에 강제 설치하지 않는다.
4. 재설치는 중복 hook/skill/규칙을 만들지 않는다. 사용자 수정 파일은 자동 덮어쓰기/삭제하지 않는다.
5. 제거는 우리 파일·설정만 제거한다. Worktrunk 자체, 기존 에이전트 플러그인, 워크트리와 캐시는 제거하지 않는다.
6. runtime 어댑터는 특정 워크트리가 사라져도 실행 가능한 안정 경로에 둔다.
7. 새 워크트리/이전 브랜치에서도 정책·hook이 로드되는지 검증한다. 추적 설정, 사용자 범위 조건부 hook, 로컬 배치 방식 중 지원 버전에 맞는 경로를 선택하고 전체 사용자 설정/비밀 파일을 복사하지 않는다.
8. CLI의 절대 경로/비대화형 PATH, 업그레이드 후 경로 변경, 재시작/신뢰 상태를 doctor로 진단한다.
9. Worktrunk의 코드/브랜치 삭제·force·reap 설정을 자동 변경하지 않는다. 캐시 정책과 분리해 설명한다.

## 8. 수용 시험

- iOS/비iOS/명시 disable/macOS-only/일반 SPM/모노레포 조건이 맞게 적용된다.
- 에이전트의 생성·재개·참조·제거는 Worktrunk를 사용하고 기존 작업 참조가 새 생성을 일으키지 않는다.
- wt 생성 → prepare → 기존 빌드, wt 제거 전 검사 → 실제 제거 → release 순서가 맞는다.
- 미승인/생략 hook, 동시 hook, 실패/재시도, 늦은 callback, 외부 삭제에도 참조가 조기 해제되지 않는다.
- 제거 실패 시 캐시는 남고, A 제거 후 B 참조와 빌드는 정상이다.
- 설치/갱신/제거가 반복 가능하며 사용자 설정과 이미 설치된 Worktrunk 연결을 보존한다.
- 새 워크트리에서도 캐시 hook이 실행되고 비대상에는 영향이 없다.
- 생성 hook의 DerivedData binding이 Claude/Codex의 기존 xcodebuild/XcodeBuildMCP에 적용된다. 전환·재시작·동시 세션에도 A/B 경로가 섞이지 않는다.
- binding 누락/충돌 때 새 관리 DerivedData를 생성·등록·재적용하고 클린 빌드로 진행한다. 정상 binding이 남은 단순 MCP 기본값 초기화는 기존 경로를 재적용해 불필요한 새 폴더를 만들지 않는다. 복구 경로도 status/release/GC 대상이며 기본 공용 위치를 사용하지 않는다. [체크리스트](08-post-implementation-checklist.md)에 실제 사용 검증을 기록한다.
- Xcode 빌드·실행·테스트 명령과 에이전트 호스트의 신뢰 정책은 유지된다.

이 항목은 설치 스크립트의 구현 명세이며 실제 스크립트나 설치된 skill/hook이 아니다.

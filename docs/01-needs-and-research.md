# 요구사항 재정의와 기존 프로젝트 조사

조사일: 2026-11-02, Asia/Seoul. 공식 문서, 유지관리자가 제공한 README, GitHub 메타데이터, 일부 소스 및 이슈를 검토했다. 이 비교표는 제품 설치·실행 비교 결과가 아니다. 추가 요청에 따른 실제 혼합 의존성 샘플 실험은 [별도 보고서](04-poc-results.md)에 구분한다. 검색 결과의 소개 글은 후보 발견에만 사용하고 아래 판단은 1차 출처에 근거한다.

## 1. 해결하려는 문제

최종 방향 갱신: **워크트리 관리·모니터링은 Worktrunk에 맡기고, 우리 도구는 빌드 캐시·DerivedData 격리·GC에 집중한다.** 첫 버전은 Worktrunk 연동을 기본 전제로 한다. 빌드·실행은 기존 Xcode 도구가 수행한다. 아래 통합 관리 니즈는 조사 기록이며 전부 첫 버전 범위는 아니다. Tuist는 필수가 아니다. 최신 범위는 [개발 플랜](05-development-plan.md)을 따른다.

핵심 요구는 **AI 개발 작업이 생성한 자원의 위치·소유자·필요 기간을 추적하면서, 유효한 빌드 캐시를 유지하고 끝난 작업의 공간을 회수하는 것**이다.

| 현재 문제 | 근본 원인 | 필요한 기능 |
| --- | --- | --- |
| 작업하던 워크트리를 찾지 못함 | 작업, 에이전트 세션, Git 경로의 연결 정보가 흩어짐 | 프로젝트별 전역 목록, 작업 이름, 브랜치/HEAD, 실제 경로, 다시 열기 |
| 새 워크트리마다 캐시를 다시 만듦 | 임의의 경로와 작업 디렉터리, 재사용 계층 부재 | 안정적인 작업별 경로, 컴파일 캐시 재사용, 의존성 다운로드 재사용 |
| 잘못된 캐시 공유로 빌드 실패 | 변경 가능한 빌드 상태를 여러 작업이 동시에 수정 | 작업별 격리, 빌드별 사용권, 공유 가능한 계층만 공유 |
| 시뮬레이터와 테스트 클론 증가 | 매번 생성하고 종료 후 소유권을 잃음 | 제한된 재사용 풀, 동시 테스트 격리, 만료 처리 |
| 완료·중단한 작업이 계속 공간을 차지 | 종료 상태와 정리 정책이 연결되지 않음 | 일시중지/완료/폐기 구분, 안전 조건 확인 후 정리 |
| 테스트 데이터·이미지·로그가 누적 | 출력 경로와 보존 기준이 없음 | 산출물 등록, 재생성 근거, 보존 기간, 결과물 고정 |
| 청소 후 다음 작업이 느려짐 | 용량만 보고 재생성 비용을 무시 | 회수 용량과 재빌드·재다운로드 비용을 함께 판단 |

제품 성공 기준은 ‘몇 GB를 지웠는가’와 함께 ‘작업 유실·빌드 충돌 없이 얼마나 안정적으로 공간을 유지하는가’여야 한다.

## 2. 조사에서 확인한 기술적 전제

### 2.1 DerivedData 전체 공유는 기본 전략으로 삼지 않는다

Apple은 Xcode 26에 Swift/C 계열의 compilation caching을 도입했고, 동일 입력의 컴파일 결과를 재사용하도록 설명한다. 설정 이름은 `COMPILATION_CACHE_ENABLE_CACHING`이며 진단용 설정도 존재한다. 이것은 모든 빌드 단계가 생략된다는 뜻이 아니다. [Apple 릴리스 노트](https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes), [빌드 설정](https://developer.apple.com/documentation/xcode/build-settings-reference)

Tuist는 워크트리 사이에서 공유 캐시는 유지하되 SwiftPM의 변경 가능한 `--scratch-path`/`.build`는 분리하도록 권고한다. `--cache-path`는 다른 계층이다. 이 구분을 본 제품의 기본 원칙으로 채택한다. [Tuist 워크트리 가이드](https://tuist.dev/en/docs/guides/features/projects/best-practices#agentic-coding-and-worktrees)

MobileBuildMCP 저장소에는 서로 다른 작업이 동일한 명시적 DerivedData 경로를 사용할 때 충돌한다는 실제 보고가 있다. 이슈는 닫혀 있으므로 현재 버전에도 같은 버그가 있다고 단정하지 않는다. 격리 필요성을 보여주는 사례로만 사용한다. [이슈 #340](https://github.com/getsentry/MobileBuildMCP/issues/340)

### 2.2 캐시가 존재하는 것과 다른 경로에서도 적중하는 것은 별개다

Tuist의 Xcode cache 문서는 공유 compilation store 보존을 권하고, Xcode 27 이후 경로 매핑 및 커버리지와의 상호작용을 설명한다. 따라서 ‘같은 저장소니까 캐시가 반드시 적중한다’고 약속할 수 없다. 실제 프로젝트의 절대 경로, 생성 코드, 매크로, 빌드 설정과 Xcode 버전별 검증이 필요하다. [Tuist Xcode cache](https://tuist.dev/en/docs/guides/features/cache/xcode-cache)

설계 판단: 기존 Xcode 프로젝트를 그대로 사용하고 Xcode 기본 캐시를 평가한다. 재사용률이 부족하면 도구 버전·입력·경로별 원인을 진단하고 안전한 격리 빌드로 동작한다. 이를 해결하기 위해 Tuist 도입을 요구하지 않는다.

### 2.3 Codex 자체 정리와 유틸의 정리가 충돌할 수 있다

현재 공식 문서에는 Codex 관리 워크트리의 기본 보유 수, 자동 정리, 고정된/진행 중인 채팅 보호, 삭제 전 스냅샷과 복원 기능이 설명돼 있다. 생성 루트도 설정할 수 있다. 이 때문에 유틸은 ‘누가 만든 워크트리인가’를 기록하고 관리 주체 간 정리 정책을 조정해야 한다. 이 설명은 사용자가 잃어버린 워크트리의 원인을 확정하는 진단은 아니다. [OpenAI 워크트리 문서](https://learn.chatgpt.com/docs/environments/git-worktrees)

Codex의 local environment setup script는 새 워크트리에 관리 정보를 등록하는 접점이 될 수 있다. [OpenAI local environments](https://learn.chatgpt.com/docs/environments/local-environment)

### 2.4 규칙 문서만으로 완전한 실행 강제는 불가능하다

Claude Code에는 `WorktreeCreate`, `WorktreeRemove`, `PreToolUse` 등이 있고 생성 hook은 기본 생성 동작을 대체한다. Bash로 직접 실행한 `git worktree add`는 생성 hook의 적용 범위 밖이다. [Claude hooks](https://code.claude.com/docs/en/hooks)

Codex의 `.rules`는 공식 문서상 sandbox 밖 명령에 대한 제어이며 실험적 기능이다. 이것을 모든 도구·프로세스를 감시하는 OS 수준 정책으로 간주하면 안 된다. [OpenAI rules](https://learn.chatgpt.com/docs/agent-configuration/rules)

설계 판단: 에이전트의 워크트리 작업은 Worktrunk로 안내하고 Worktrunk hook에서 우리 캐시 준비·해제 검사를 호출한다. 기존 빌드 실행은 그대로 둔다. hook 미승인/우회와 지원되지 않는 내장 생성 흐름을 포함한 모든 실행을 강제한다고 보장하지 않는다. 우리 GC는 실제 상태를 별도로 대조한다.

## 3. 기존 제품·프로젝트 비교

‘미확인’은 공개 문서에서 해당 요구를 확인하지 못했다는 의미다. 기능 부재를 완전히 증명한 것은 아니다.

| 후보 | 확인한 강점 | 이번 요구에 남는 부분 | 활용 판단 |
| --- | --- | --- | --- |
| [SimCleaner](https://github.com/Stepanokdev/SimCleaner) | 시뮬레이터·런타임·DerivedData·AI 빌드 작업 공간, Smart Cleanup, CLI, MIT | Git 작업 완료/폐기와 자원 연결, 공통 캐시 할당은 미확인 | 안전한 정리 및 후속 시뮬레이터 관리의 보조 참고 |
| [Mole (`tw93/Mole`)](https://github.com/tw93/Mole) | CLI 디스크 분석, 프로젝트 산출물 정리, dry-run, 보호 목록, JSON 조회·이력 | 작업별 소유권·사용권, 의존성별 캐시 정합성 및 재사용 조정은 미확인 | CLI·산출물 정리 UX의 주요 참고; 필수 실행 의존성 아님 |
| [DevCleaner.app](https://devcleaner.app/) | 메뉴바 UI, 개발 캐시 탐색, 미리보기, Pro 자동 정리 | 작업 수명주기와 캐시 재사용 조정은 미확인 | 즉시 사용 가능한 메뉴바 청소 대안 |
| [DevCleaner for Xcode](https://github.com/vashpan/xcode-dev-cleaner) | DerivedData, DeviceSupport, 아카이브, 로그 등 Xcode 전용 탐색 | 워크트리·공유 캐시·시뮬레이터 풀은 미확인 | 기존 Xcode 청소 UX 참고 |
| [Worktrunk](https://worktrunk.dev/) | AI 병렬 작업용 워크트리 생성·목록·정리, hooks, APFS CoW 복사 | Xcode 캐시 호환성과 빌드 데이터 보존/회수 정책은 별도 | 첫 버전의 워크트리 관리 도구로 채택. 우리 도구는 캐시 hook으로 연동 |
| [Git Worktree Runner](https://github.com/coderabbitai/git-worktree-runner) | 생성 위치 통일, 설정 파일 복사, setup hooks, 에디터/AI 실행 | Apple 자원 관리와 메뉴바는 별도 | 작은 스크립트 기반 대안 |
| [Tuist](https://tuist.dev/en/docs/guides/features/cache/xcode-cache) | Xcode compilation cache 연동 및 원격 재사용, 별도의 module cache | 디스크·워크트리·시뮬레이터의 통합 수명주기 관리 | 설계 참고, 향후 선택 호환 대상; 필수 의존성 아님 |
| [MobileBuildMCP](https://github.com/getsentry/MobileBuildMCP) | iOS/macOS 빌드·테스트·시뮬레이터용 MCP와 CLI | 전체 디스크 예산과 워크트리 보존 정책은 별도 | 기존 사용자에게 빌드 어댑터 제공 |
| [Xcodes](https://github.com/XcodesOrg/XcodesApp) | 여러 Xcode 및 플랫폼/runtime 설치·전환 | 작업별 캐시와 산출물 정리 | 툴체인 관리의 보완재 |
| [XcodeReclaim](https://github.com/YamanBoztepe/XcodeReclaim) | Xcode 잔여 데이터 목록, 사용 중 자원 삭제 제한, 계층화된 Swift 코드 | 작업 수명주기, 캐시 재사용, 메뉴바 중심 자동화 | 소형 네이티브 앱 구조 참고 |

DevCleaner.app와 vashpan의 DevCleaner for Xcode는 **동일한 제품이 아니다**. 비교·설치 시 이름만 사용하지 않는다.

### 3.1 안전한 정리의 참고: SimCleaner

README뿐 아니라 `SmartCleanup.swift`도 확인했다. 해당 코드는 계획 작성 시 폴더의 볼륨/inode를 기록하고, 실행 전에 동일 객체인지와 최근 활동을 다시 확인한다. 공유 DerivedData 폴더는 자동 정리에서 제외하며 활동 시점을 읽지 못하면 오래됐다고 판단하지 않는다. 참고할 가치가 큰 설계다. 이것만으로 전체 삭제 엔진의 안전성을 검증했다고 보지는 않는다. [검토한 소스](https://github.com/Stepanokdev/SimCleaner/blob/main/Packages/SimCleanerKit/Sources/SimCleanerKit/SmartCleanup.swift)

공개 README상 무료 App Store/Pro 에디션이 있고 요구 OS와 가능한 기능이 다르다. Pro는 조사 당시 macOS 27/Xcode 27을 요구한다. 사용자의 설치 환경에 맞는지 확인한 뒤 선택해야 한다. [에디션 설명](https://github.com/Stepanokdev/SimCleaner#two-editions)

### 3.2 워크트리 관리를 맡길 도구: Worktrunk

사용자 선택에 따라 단순 참고를 넘어 워크트리 관리 자체를 Worktrunk에 맡긴다. 생성·전환·조회·참조·제거 명령을 우리 CLI에 다시 만들지 않는다. [개요](https://worktrunk.dev/), [switch](https://worktrunk.dev/switch/)

`wt list --format=json`은 자동화용 조회를 제공한다. 연결된 작업을 확인할 때 공개 schema/version을 검증하고 터미널 표를 파싱하지 않는다. Git 자체 조회는 자원 참조/제거 사실을 대조하는 근거이며 독립 워크트리 관리자 fallback이 아니다. [목록/JSON](https://worktrunk.dev/list/)

hook은 생성·전환·제거와 연결할 수 있지만 background 실행과 우회가 가능하다. 우리 prepare/check-release/release와 GC는 준비·사용·실제 제거 상태를 확인해야 한다. hook만으로 외부 Xcode의 사용 상태를 완전히 안다고 보장하지 않는다. [hook 동작](https://worktrunk.dev/hook/)

Worktrunk의 `copy-ignored`는 지원 파일시스템에서 CoW 복사를 한다. 파일은 별도 경로를 가지며 변경 전 디스크 블록을 공유한다. 동일한 변경 가능 디렉터리를 symlink로 공유하는 방식과 구별된다. [복사 동작](https://worktrunk.dev/step/#copy-on-write)

설계 판단: 허용된 의존성 파일의 초기 배치에 실험할 수 있다. 하지만 실행 중인 DerivedData의 폴더별 복사가 일관된 스냅샷을 보장하거나, 경로가 바뀐 빌드 DB가 그대로 재사용된다고 가정하지 않는다. DerivedData 전체 복제는 MVP 기본값에서 제외한다.

Worktrunk는 에이전트별 활동 표시도 제공하나 hook 지원 범위가 다르다고 명시한다. 활동 표시는 보조 신호이며, 표시가 사라졌다고 작업 결과를 삭제해도 된다는 뜻은 아니다. [Worktrunk agent integration](https://worktrunk.dev/claude-code/)

관리·모니터링은 Worktrunk 목록과 활동 표시를 그대로 사용한다. 우리 status는 캐시 용량·참조·격리/GC 상태만 보완한다. Worktrunk의 branch 삭제·프로세스 정리 정책을 우리 설치기가 변경하지 않는다. 제거 요청과 완료를 구분해 실제 완료 후에만 캐시 참조를 해제한다. [목록](https://worktrunk.dev/list/), [제거](https://worktrunk.dev/remove/)

에이전트 설치는 기존 Worktrunk 연결을 감지·재사용하고, iOS 대상의 Worktrunk 사용 안내와 캐시 hook만 추가한다. Claude/Codex별 지원 범위를 확인하며 같은 생성·제거 hook을 우리 패키지에 중복 구현하지 않는다. [에이전트 설치 설계](07-agent-integration.md), [Worktrunk 에이전트 연동](https://worktrunk.dev/claude-code/)

### 3.3 Mole — CLI와 산출물 정리 참고

사용자 요청으로 2026-11-02 추가했다. 공식 README와 보안 정책을 검토했으며 설치·실행 검증은 하지 않았다. 무료 CLI `mo`와 별도 배포되는 네이티브 Mole for Mac을 구분한다. [공식 저장소](https://github.com/tw93/Mole)

참고할 동작은 다음과 같다.

- `mo purge`: 프로젝트별 산출물 후보와 삭제 전 미리보기. 최근 활동 또는 활동 확인 불가 항목은 기본 미선택이며 Git 추적 파일·중첩 저장소 등을 보호한다고 설명한다.
- `--dry-run`, 보호 목록, `mo history --json`: 대상 확인, 제외 설정, 실행 이력.
- `mo analyze --json`: 측정 불가와 부분 스캔을 빈 폴더와 구별하는 `scan_status`. 자동화는 종료 코드만으로 완전한 조회라고 판단하지 않아야 한다.

이는 공식 문서상 동작이며 전체 삭제 엔진의 안전성 검증은 아니다. `clean`/`purge`는 영구 삭제라고 명시돼 있다. [README](https://github.com/tw93/Mole#readme), [보안 정책](https://github.com/tw93/Mole/blob/main/SECURITY.md)

우리 계획에는 **프로젝트별 미리보기, 보호/제외 이유, 부분 조회 표시, 구조화된 이력**을 참고한다. 재생성 가능성은 폴더 이름만으로 결정하지 않고 등록된 생성 근거·소유권·사용권으로 판단한다. 캐시를 지우면 다음 빌드가 얼마나 느려지는지도 함께 표시한다. Mole의 일괄 정리를 내부 삭제 엔진으로 자동 호출하는 것은 현재 범위에 넣지 않는다.

공식 저장소는 GPL-3.0으로 표시한다. 현재는 동작·UX 참고이며 코드 편입 결정이 아니다. 워크트리 수명주기나 혼합 의존성 캐시 정합성을 대체한다고 확인하지 못했으므로 보완재로 평가한다.

### 3.4 코드 참고·도입 전 확인할 사항

2026-11-02 GitHub API 조회 기준 아래 저장소는 archived 상태가 아니었다. 마지막 push는 유지관리 활동의 단서일 뿐 품질이나 호환성 보증이 아니다.

| 저장소 | 마지막 push 날짜, UTC | 저장소가 표시한 라이선스 |
| --- | --- | --- |
| [SimCleaner](https://github.com/Stepanokdev/SimCleaner) | 2026-10-14 | MIT |
| [Worktrunk](https://github.com/max-sixty/worktrunk) | 2026-11-01 | 공식 사이트에 MIT OR Apache-2.0 표기 |
| [Git Worktree Runner](https://github.com/coderabbitai/git-worktree-runner) | 2026-10-28 | Apache-2.0 |
| [MobileBuildMCP](https://github.com/getsentry/MobileBuildMCP) | 2026-11-01 | MIT |
| [Xcodes](https://github.com/XcodesOrg/XcodesApp) | 2026-10-13 | MIT |
| [XcodeReclaim](https://github.com/YamanBoztepe/XcodeReclaim) | 2026-10-08 | MIT |
| [DevCleaner for Xcode](https://github.com/vashpan/xcode-dev-cleaner) | 2026-05-18 | GPL-3.0 |

DevCleaner for Xcode의 README는 macOS 14/Xcode 15 테스트를 명시하고 있어 최신 Xcode 호환성은 별도 확인 대상이다. XcodeReclaim README에는 서명된 다운로드가 아직 없다고 적혀 있다. 따라서 두 도구를 곧바로 최신 환경용 완제품으로 추천하지 않는다. 코드 재사용 시에는 각 LICENSE와 의존성 고지를 직접 확인한다.

## 4. 기성품으로 해결할 것인가

### 대안 A: 즉시 불편을 줄이는 조합

- 메뉴바 정리: DevCleaner.app. 자동화는 Pro 기능이라고 안내한다.
- 워크트리 정리: Worktrunk 또는 Git Worktree Runner 중 하나.
- 빌드: 워크트리별 DerivedData + Xcode compilation caching.
- 필요할 때 시뮬레이터 상세 관리: SimCleaner.
- CLI 기반 용량 분석·산출물 정리 후보 검토: Mole. 작업·빌드 캐시 정책은 별도 관리한다.

평가: 설치와 운영 규칙만으로 시작할 수 있다. 하지만 여러 도구의 정책을 맞춰야 하고, ‘이 작업에 속한 모든 일회성 데이터’라는 공통 목록은 직접 보완해야 한다. 여러 cleaner가 같은 경로를 자동 정리하도록 동시에 켜지 않는다.

### 조사한 대안 B: Tuist cache — 현재 계획에서 채택하지 않음

조사에서는 기존 Xcode 프로젝트에 Tuist compilation cache를 연동하는 대안도 확인했다. 프로젝트 생성기를 사용하는 것과 캐시 서비스를 도입하는 것은 별개지만, 사용자 조건에 따라 현재 제품 계획은 어느 쪽도 요구하지 않는다. 향후 이미 Tuist를 쓰는 프로젝트와의 호환을 검토할 수 있으며, 비-Tuist 사용자의 기본 경로와 분리한다.

### 채택 방향: Worktrunk + 캐시·DerivedData·GC CLI

공개 자료에서 요구사항 전체를 충족한다고 확인한 단일 제품은 찾지 못했다. 다만 청소와 워크트리 기능을 각각 처음부터 새로 만드는 필요성은 낮다.

Worktrunk가 워크트리 관리와 모니터링을 맡는다. 우리 도구는 **호환 캐시 준비**, **DerivedData 격리와 안정적 경로**, **참조 추적·전용 잔여물 GC**만 맡는다. 빌드는 기존 Xcode 도구가 수행한다. 설치기는 Worktrunk 사용 안내와 iOS cache hook을 연결한다. 실제 경로 적용·외부 사용 확인·hook 실패 복구는 검증 과제로 남고, 설치·연동 시험은 아직 하지 않았다. 시뮬레이터·주기 정리·메뉴바는 후속이다.

## 5. 확인하지 않은 것

- 실제 Mac 전체의 워크트리 수·캐시 크기·회수 가능 용량.
- 사용자의 실제 프로젝트들의 캐시 적중률과 빌드 시간 개선 폭. 합성 샘플은 별도 측정했다.
- Codex/Claude 설치 버전별 hook 적용 범위와 타 앱 정리 정책의 실제 설정.
- 유실된 워크트리의 원인 및 복원 가능 여부.

조사 시작 시 작업 디렉터리는 비어 있었고 Git 저장소가 아니었다. 처음 `xcodebuild -version`은 active developer directory가 `/Library/Developer/CommandLineTools`여서 실패했다. 후속 확인에서 `/Applications/Xcode.app`의 Xcode 27.0을 찾았고, 프로세스별 `DEVELOPER_DIR` 지정으로 샘플을 빌드했다. 전역 Xcode 선택 설정은 바꾸지 않았다.

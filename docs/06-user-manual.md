# 사용자 매뉴얼 — Worktrunk와 함께 캐시 관리하기

2026-11-02, idutil 0.2.0. 구현된 동작을 설명한다. 검증 범위와 남은 제약은 [구현 결과](10-implementation-status.md)를 기준으로 한다.

## 처음 한 번 연결하기

[설치 안내](11-installation.md)에 따라 릴리즈 바이너리와 Worktrunk를 설치한다. Python/uv는 필요 없다. 적용할 iOS Git 저장소로 이동해서 실행한다.

```sh
idutil setup --ios --project App.xcworkspace --scheme App --agent all --dry-run
idutil setup --ios --project App.xcworkspace --scheme App --agent all
```

`.xcodeproj`도 지정할 수 있다. `--ios`는 이 저장소를 명시적으로 등록한다. 자동으로 모든 Xcode 프로젝트에 적용하지 않는다. `scripts/install-agent.sh`도 같은 setup 옵션을 받는다.

설치 내용은 Worktrunk 사용자 설정의 세 hook, 프로젝트의 `.agents/skills/ios-disk-util`, `.claude/skills/ios-disk-util`, AGENTS.md/CLAUDE.md의 관리 블록이다. 기존 hook과 사용자 문구를 보존하며, 수정된 관리 파일은 경고 후 보존한다. 다른 등록되지 않은 저장소에서는 hook이 아무 작업 없이 끝난다. 전역 에이전트 설치나 실제 호스트 세션을 자동으로 실행하지 않는다.

기본 관리 위치는 `~/Library/Application Support/idutil`이다. 변경하려면 모든 호출에 같은 `--root <경로>`를 명령 앞에 지정하거나 `IDUTIL_ROOT`를 설정한다. 관리 루트는 워크트리 밖에 있어야 한다. 설치된 hook은 당시의 절대 경로와 별도 보관한 실행 코드를 사용한다.

local SPM, 개발용 Pod, vendored framework처럼 별도로 관찰할 입력은 `--input Dependencies --input Vendor`로 등록한다. 파일 내용 hash를 관찰하지만 전체 컴파일 의존성 그래프를 대신하지 않는다. `--protect ReleaseOutput`은 워크트리 제거 전 보존할 출력 경로를 지정한다. 업데이트에 같은 옵션을 다시 주면 목록을 교체하므로 전체 목록을 전달한다.

## 작업 시작과 재개

```sh
wt switch --create feature-login --base main
idutil prepare --json
```

생성 hook이 작업별 DerivedData, SourcePackages, Artifacts 폴더와 소유 기록을 만든다. 전환·재개 후에는 prepare로 현재 기록을 다시 읽는다. 유효한 경로는 재사용하므로 재개할 때마다 새 폴더를 만들지 않는다.

`buildContext`에는 경로, 자원 ID, `xcodebuildArgs`, 환경 변수, `mcpSessionDefaults`가 있다. 에이전트는 이를 현재 빌드 도구에 적용해야 한다. hook에서 셸 환경을 export하는 것만으로 부모 에이전트나 이미 실행 중인 MCP의 설정이 바뀌지는 않는다.

사람이 직접 빌드할 때도 출력된 `derivedDataPath`를 기존 xcodebuild의 `-derivedDataPath`에 지정한다. SPM checkout 경로와 캐시 옵션까지 일치시키려면 JSON의 전체 `xcodebuildArgs`를 사용한다. JSON 문자열을 셸 코드로 eval하지 않는다. 프로젝트 고유의 출력 경로 override가 있으면 `doctor --build-settings <JSON>`과 실제 결과를 확인한다.

## XcodeBuildMCP 사용

현재 워크트리의 `mcpSessionDefaults`를 `session_set_defaults`로 적용하고 `session_show_defaults`로 확인한다. `clearMCPDefault`가 가리키는 이전 project/workspace 키도 해제한다. 정확한 입력 schema는 설치한 MCP 버전에서 확인한다. 병렬 작업은 MCP 세션을 분리한다.

검증한 **2.7.0의 macOS 빌드**는 지정한 DerivedData를 사용한다. 하지만 테스트의 후반 `test-without-building` 단계는 `-derivedDataPath`를 누락해 기본 DerivedData에 테스트 메타데이터를 생성했다. 해당 버전의 테스트는 같은 context를 전달한 기존 `xcodebuild test`를 사용한다. MCP의 별도 로그·테스트 결과 저장소는 idutil GC 대상이 아니다. 다른 버전과 다른 동작은 추가 검증이 필요하다.

idutil은 빌드를 가로채는 wrapper가 아니다. 규칙을 무시한 호출이나 외부 도구 내부의 누락을 강제로 차단할 수 없다. Xcode GUI의 Build 버튼도 별도 설정·검증이 필요하다.

## 캐시 오류에서 계속 작업하기

설정만 사라졌으면 prepare 결과의 기존 경로를 다시 적용한다. 실제 binding/폴더 손상은 prepare가 새 관리 경로로 복구한다. 구체적인 캐시 오류 때문에 새 세대가 필요하면 다음과 같이 요청한다.

```sh
idutil prepare --fresh --expected-resource <실패한-resourceId> --reason cache-error --json
```

공유 compiler cache가 의심되면 `--no-shared-cache`를 추가한다. 반환된 경로로 다시 빌드한다. 이 우회는 이후 세대에도 유지되며, 명시적 `setup --shared-cache`로 다시 활성화한다. 일반 소스 오류나 테스트 실패마다 새 캐시를 만들지는 않는다.

새 세대도 동일한 관리 루트와 소유·참조·GC 규칙에 속한다. 이전 데이터는 즉시 지우지 않는다. 클린 빌드는 감수하되 공용 DerivedData로 몰래 돌아가지 않는다. 저장장치 부족이나 권한 오류로 폴더 자체를 만들 수 없으면 원인을 보고한다.

## 공간 확인과 정리

```sh
idutil status --measure
wt remove feature-login
idutil gc
```

Worktrunk 제거 후 hook은 실제 경로 제거를 확인하고 참조를 해제한다. hook을 놓친 경우에도 GC 계획 시 Git/파일시스템 상태를 재확인한다. 사용 종료가 불확실하면 후보에서 제외한다. 현재 버전은 제거 직후 자동 삭제하지 않는다.

GC 계획은 기본 24시간 보존 기간이 지난, 참조가 해제되거나 교체된 **idutil 소유 자원**만 대상으로 삼는다. 빌드 도구를 종료하고 정리 중에도 빌드하지 않을 상태에서 적용한다.

```sh
idutil gc --apply <planId> --quiescent
```

계획은 15분 동안 유효하다. 적용 시 소유권·경로·변경 여부·참조를 다시 확인한다. Xcode 등 빌드 관련 프로세스가 보이면 삭제를 거부한다. 논리적 파일 크기와 실제 디스크 회수량은 APFS 등에 따라 다를 수 있다.

`.xcarchive`, `.dSYM`, `.xcresult`, `.idutil-keep`가 있는 자원은 통째로 보존한다. 재생성 가능한 임시 출력은 반환된 Artifacts 경로에 두면 해당 세대와 함께 관리된다. 임의의 프로젝트 폴더, 시뮬레이터, 전역 다운로드 캐시, native CAS 내부는 지우지 않는다. 중요한 테스트 결과와 디버깅 자료를 보존한 탓에 회수되지 않는 데이터가 있을 수 있다.

## 진단과 연결 해제

`doctor --integration`은 hook과 현재 binding을 점검한다. `--effective-settings <JSON>`은 MCP defaults 객체 스냅샷, `--build-settings <JSON>`은 실제 xcodebuild `-showBuildSettings -json` 출력과 비교한다. 스냅샷 일치는 지금 실행 중인 도구의 사용을 보증하지 않는다.

```sh
idutil setup --update --dry-run
idutil setup --update
idutil setup --uninstall --dry-run
idutil setup --uninstall
```

해제는 수정되지 않은 설치 파일·관리 블록만 제거한다. 워크트리·캐시·기존 사용자 문구는 유지한다. Git info/exclude의 로컬 무시 항목도 남긴다. 관리 중인 다른 저장소가 있으면 공통 hook은 유지한다.

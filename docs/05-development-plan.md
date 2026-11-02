# 개발 플랜 — Worktrunk 연동 캐시·DerivedData·GC CLI

갱신일: 2026-11-02. 구현 승인 후 0.1.0 CLI를 구현했다. 이 문서는 목표 설계이며, 현재 기능·검증 범위·제약은 [구현 결과](10-implementation-status.md)와 [사용자 매뉴얼](06-user-manual.md)이 우선한다. 전체 설계 검토는 [검토 기록](09-plan-review.md)에 정리했다.

## 1. 제품 범위

| 구성 | 담당 |
| --- | --- |
| Worktrunk | 워크트리 생성·전환·조회·참조·제거, Git/에이전트 활동 표시 |
| idutil | 전용 빌드 경로 준비·등록, 검증된 공통 캐시 연결, 복구·참조·GC |
| Xcode/xcodebuild/XcodeBuildMCP | 기존 방식의 빌드·실행·테스트와 컴파일 캐시 적중 판단 |
| 에이전트 연동 규칙 | 현재 binding을 기존 빌드 도구에 적용·확인하고 필요하면 idutil에 복구 요청 |

첫 버전은 Worktrunk 연동을 전제로 한다. 자체 워크트리 관리자, 빌드 wrapper, 에이전트 세션 관리자를 만들지 않는다. Tuist·XcodeGen은 필수가 아니다. SCM SPM/local SPM/CocoaPods/static framework는 각각 단독 또는 조합해서 지원한다.

첫 버전의 재사용은 **공식 다운로드 캐시와 검증된 Xcode compilation store 연결**에 집중한다. 자체 seed 저장소·임의 배포물 복사 엔진은 후속 설계로 분리한다. 의존성 지원은 그 방식의 캐시를 직접 새로 구현한다는 뜻이 아니다. [방식별 기준](03-mixed-dependency-consistency.md)

## 2. 사용자 흐름

1. setup으로 적용할 iOS 프로젝트·빌드 프로필·관리 루트와 Worktrunk/에이전트 연결을 등록한다.
2. Worktrunk로 새 워크트리를 만들면 생성 hook이 prepare를 호출한다.
3. prepare가 전용 경로와 참조를 등록하고 build context를 반환한다.
4. 에이전트는 해당 context를 기존 빌드 도구에 적용·확인한다. 빌드는 그 도구가 수행한다.
5. 정상 전환·재개에는 같은 경로를 재사용한다. binding/자원 손상은 새 관리 경로로 복구하고 클린 빌드한다.
6. Worktrunk가 실제 제거한 뒤 release가 참조를 해제한다. 정책과 사용 종료 조건을 만족하는 전용 데이터만 GC한다.

사람이 Xcode GUI를 사용하는 경우 같은 원칙으로 경로를 설정하되 별도 어댑터 검증이 필요하다. CLI/MCP 설정으로 GUI까지 자동 적용됐다고 보장하지 않는다.

## 3. 명령과 공통 상태

가칭이며 미구현이다. 복구를 비공개 명령에 의존하지 않도록 prepare를 공개한다.

| 명령 | 역할 |
| --- | --- |
| idutil setup | 프로젝트·정책·hook·에이전트 연결 설치/갱신. --dry-run 지원 |
| idutil prepare | 기존 워크트리의 빌드 공간 준비/재사용. --worktree와 --json 지원. --fresh는 새 전용 세대로 복구 |
| idutil status | 경로·참조·용량·마지막 진단을 조회. --json에 build context 포함 |
| idutil doctor | 현재 경로/설정/소유권 문제 진단. 빌드 실행·삭제·암묵적 복구 없음 |
| idutil gc | 삭제 계획. --apply <plan-id>로 실행 직전 재검증 후 적용 |

check-release/release는 hook 전용 내부 동작이다. status/doctor는 읽기 전용이고, 준비/복구의 쓰기는 모두 prepare를 통한다. 설치 스크립트는 setup을 호출하는 얇은 진입점이며 별도 설치 엔진이 아니다.

하나의 종합 ready 값으로 모든 것을 표현하지 않는다.

| 구분 | 의미 |
| --- | --- |
| 준비 결과 | prepared 또는 error. 경로·등록 완료 여부 |
| 캐시 모드 | shared / private-only, 미확인은 unknown. 연결한 계층과 복구 여부·이유 별도 표시 |
| 설정/사용 증거 | 도구·binding 세대·확인 시각이 있는 마지막 증거 또는 unknown |

prepare 성공은 빌드 도구 설정 확인과 다르다. MCP의 현재 메모리는 에이전트가 그 도구에서 직접 확인한다. idutil status는 근거 없이 이를 실시간 확인됨으로 표시하지 않는다. 상시 수집 서버나 세션 DB는 만들지 않는다.

캐시 모드는 idutil이 설정·확인한 빌드 캐시 연결 범위의 표시다. private-only가 시스템 전체의 패키지 다운로드 캐시까지 껐다는 뜻은 아니다. 기존 도구의 다른 계층은 확인된 연결별로 표시한다.

## 4. Worktrunk hook 연결

| 접점 | 동작 | 완료 기준 |
| --- | --- | --- |
| pre-start | prepare | 경로 등록과 context 게시 완료. 실제 도구 설정은 후속 에이전트 단계 |
| pre-remove | check-release | 제거 대상 내부의 알려진 참조/보호 자료 위험 점검, pending 기록. 데이터 삭제 없음 |
| post-remove | release | 실제 제거를 대조하고 참조 해제. 사용 중/불명 자원은 GC 보류 |

pre-start는 생성 시 실행되며 기존 워크트리 전환 때마다 실행되는 접점이 아니다. 따라서 전환·재개 뒤 경로 확인은 에이전트 연동 규칙이 담당한다. 동기 준비가 끝나기 전에 빌드하는 병렬 hook을 설치하지 않는다. post-remove는 명시적 대상 ID/경로로 처리한다. [공식 hook 계약](https://worktrunk.dev/hook/)

hook 미승인/생략, background 제거, 실패/중복/지연 이벤트를 고려한다. 실제 제거 전에는 참조를 해제하지 않는다. status/doctor는 불일치를 표시하고 prepare/GC가 실제 상태를 재검증해 필요한 기록을 갱신한다. 워크트리 자체를 대신 생성/삭제하지 않는다.

## 5. 캐시와 DerivedData

mutable DerivedData, SourcePackages checkout, Pods 설치 결과, scratch는 작업별로 분리한다. 전체 DerivedData나 임의 .o/.swiftmodule을 복제·공유하지 않는다. 공식 원본 캐시와 검증된 compilation store만 공통으로 연결한다. 프로젝트 내부 의존성을 자동 이사하거나 pod install/update, 패키지 resolve, 빌드를 대신 실행하지 않는다.

프로필은 setup에서 명시한 workspace/project와 작업 용도 단위로 시작한다. 모든 build setting의 조합마다 새 DerivedData를 만들지 않는다. 도구/플랫폼 호환성은 별도 metadata로 기록하고 검증된 경로 분리가 필요한 경우만 프로필을 나눈다. 같은 프로필의 동시 writer는 기존 도구에서 직렬화하거나 명시적인 별도 프로필을 사용한다.

### 5.1. 에이전트의 실제 DerivedData 사용은 필수 조건

- 생성 hook이 영속 binding을 게시하고 에이전트가 읽는다. hook의 export만으로 상위 프로세스/MCP 메모리가 바뀐다고 가정하지 않는다.
- xcodebuild에는 -derivedDataPath, XcodeBuildMCP에는 지원 버전의 derivedDataPath 설정을 적용한다. 대상 workspace/project와 override도 확인한다.
- 정상 전환, MCP 재시작, 누락된 호출 인수는 **유효한 기존 binding 재적용**으로 처리한다. 다른 워크트리의 세션 기본값이 남았다는 사실만으로 정상 캐시를 버리지 않는다.
- binding 자체 누락·손상, 실제 경로 충돌/오염, 확인된 캐시 오류는 새 관리 DerivedData로 복구한다. 기본 공용 위치로 fallback하지 않는다.
- A/B가 하나의 MCP mutable 기본값을 동시에 덮어쓰는 구성은 피한다. 독립 세션 또는 검증된 호출별 설정을 사용한다.
- 지원 흐름의 규칙과 실제 검증을 요구하되 임의 셸/GUI의 모든 우회를 OS 수준으로 차단한다고 주장하지 않는다.

[설치 계약](07-agent-integration.md)과 [실측 체크리스트](08-post-implementation-checklist.md)를 따른다.

### 5.2. 캐시 정합성 판정 기준

판정은 자원·재사용 방식별로 한다. 같은 브랜치/commit/lockfile만으로 컴파일 결과의 정합성을 승인하지 않는다.

| 계층 | 확인 기준 | 실패/불명 시 |
| --- | --- | --- |
| 공식 원본 캐시 | 출처·실제 revision/checksum을 기존 패키지 도구가 검증하는 경로 | 해당 연결 제외, 기존 도구의 원본 획득 흐름 사용 |
| native compilation store | 지원 툴체인/SDK·설정·동시성 시험, 소유권과 연결 범위 | 해당 공유 제외. 개별 hit/miss는 Xcode에 맡김 |
| local/개발 Pod/vendored 입력 | 재준비·명시 진단 시 관련 내용/manifest/binary slice 확인. dirty 입력을 lockfile로 대체하지 않음 | 오류 원인 표시, 감지된 private 상태 문제는 복구 |
| private 작업 상태 | worktree ID·프로필·경로·도구 설정의 일치 | 유효 binding 재적용 또는 새 관리 경로로 복구 |
| 자체 seed | 전체 내용·출처·원본 안정성·형식/환경 검증 | 후속 기능. 첫 버전에 복사 엔진을 넣지 않음 |

reusable/incompatible/unknown과 근거·검사 시점을 기록한다. 관리 fingerprint를 자체 compiler key로 쓰지 않는다. 매 빌드 전체 소스 hash, 전이 의존성/임의 스크립트 자동 추론은 첫 버전 범위가 아니다. [상세 정합성 계약](03-mixed-dependency-consistency.md#8-정합성-판정-계약)

통과 기준은 캐시 사용/미사용 실행·테스트 결과와 의존성/리소스가 같고 입력 변경값이 정확하게 반영되는 것이다. exit code 0이나 속도 향상만으로 통과하지 않는다.

### 5.3. 캐시 오류는 관리되는 새 공간으로 자동 복구

1. 캐시가 단순히 비었으면 기존 전용 경로를 쓴다. 경로 설정만 잃었으면 재적용한다. 실제 binding/자원 문제가 있을 때 prepare --fresh로 새 세대를 준비한다.
2. 정상 생성과 같은 관리 루트·resource ID·소유권·프로필·참조·journal·GC 정책으로 등록한 뒤 binding을 게시한다. 워크트리 세대와 캐시 복구 세대는 구분한다.
3. 에이전트가 새 경로를 기존 빌드 도구에 적용·확인하고 그 도구로 빌드한다. idutil은 빌드를 실행하거나 실패를 상시 감시하지 않는다.
4. 의심되는 공유 계층은 지원 설정으로 제외/격리한다. 새 DerivedData는 증분 상태를 새로 시작한다는 뜻이며 정상 native cache까지 모두 비활성화한다는 뜻은 아니다.
5. 복구된 경로를 계속 재사용한다. 같은 이전 resource/오류에 대한 동시·중복 요청은 합치고 자동 복구는 1회로 제한한다. 일반 컴파일·서명·테스트 실패를 캐시 오류로 오인하지 않는다.
6. 이전 자원은 사용 종료 확인 전 삭제/참조 해제하지 않는다. 부분 생성도 추적하며 이전 세대와 복구 세대 모두 동일 GC에 포함한다.

복구는 경고를 남기고 개발을 계속하게 한다. 디스크 부족·권한·등록 실패·도구 설정 불가처럼 새 공간도 사용할 수 없는 오류는 구체적으로 보고한다. 비관리 임시 폴더로 우회하지 않는다. 후속 test-without-building/앱 실행만 요청한 상황에서 경로가 교체되면, 에이전트가 기존 도구로 필요한 build-for-testing/빌드를 먼저 수행해야 한다.

## 6. GC 책임

| 자원 | 처리 |
| --- | --- |
| 제거된 워크트리/복구 이전 세대의 관리 전용 데이터 | 사용 종료·소유권·보존 조건 충족 후 회수 |
| 활성 작업의 전용 데이터·다른 작업이 참조하는 공통 자원 | 유지 |
| 외부/전역 패키지 캐시, Xcode CAS 내부 객체 | 직접 삭제하지 않음 |
| archive/dSYM·최종 전달물·미해결 실패 증거·미분류 데이터 | 기본 보호 |
| 코드 워크트리·브랜치 | Worktrunk 책임 |

첫 버전은 등록된 전용 데이터의 회수 범위를 보장한다. Mac 전체 용량 상한이나 native 공통 저장소의 자동 축소를 보장하지 않는다. 공통 저장소의 용량은 별도로 표시하고 도구가 지원하는 보존/정리 기능은 후속 검증한다.

pre-remove는 제거할 소스 트리 내부의 알려진 사용/참조/보호 자료 문제를 검사한다. **외부 관리 루트의 캐시가 사용 중/불명이라는 이유만으로 워크트리 제거를 막지 않는다.** 이 경우 실제 제거 후 캐시 GC만 보류한다. 원본 경로에 종속된 다른 작업이나 내부 보호 자료처럼 워크트리 제거가 직접 손실을 일으키는 경우는 구분한다.

참조 0, 경로 부재, mtime, 에이전트 idle만으로 삭제하지 않는다. 외장 볼륨 미연결/권한 오류도 unknown으로 둔다. 잠금은 idutil 작업끼리만 유효하며 외부 writer를 통제하지 못한다. 검증된 종료 근거 또는 관련 도구를 종료한 명시적 정리 구간이 필요하다. 근거가 부족하면 보류하며 자동 kill하지 않는다.

전용 데이터도 보존 항목을 포함할 수 있으므로 폴더 전체를 무조건 삭제하지 않는다. 보호 항목을 제외해 회수할 수 없으면 그 자원을 보류한다. 재생성 가능한 테스트 출력·성공 로그는 명시 등록 경로만 포함한다.

post-remove 자동 정리는 setup에서 활성화한 정책 범위만 수행한다. 수동 GC는 계획/적용을 나누며 실행 직전 재검증한다. dry-run의 예상 용량과 실제 여유 공간 변화는 구분한다.

## 7. 에이전트 설치

설치 스크립트는 setup과 같은 로직으로 설치·갱신·제거·진단을 제공한다. Worktrunk의 기존 연결을 재사용하고 iOS 적용 범위·세 가지 cache hook·prepare/경로 적용 규칙만 보완한다. 자체 WorktreeCreate/Remove나 범용 PreToolUse 감시 계층을 중복 설치하지 않는다.

기존 설정과 사용자 수정·신뢰 정책을 보존한다. Worktrunk를 우회하는 호스트 managed-worktree 흐름은 지원 제한으로 표시한다. 등록 프로젝트의 이전 브랜치/새 워크트리에서도 규칙이 로드되는 경로를 통합 시험한다. [설치 상세](07-agent-integration.md)

## 8. 개발 순서와 통과 기준

모두 미착수다. 구현 승인 후 아래 순서로 진행한다.

| 단계 | 결과물 | 통과 기준 |
| --- | --- | --- |
| A. 최소 경로 연결 | setup/prepare/status, hook·skill, xcodebuild/MCP 설정 | A/B 실제 출력 격리, 전환 시 재사용, 누락/충돌 복구와 등록 |
| B. 정리 | check-release/release/GC, doctor, 보존 정책 | A 제거 후 B 정상, 이전 복구 세대 회수, 실패/지연/사용 불명에서 보존 |
| C. 공식 공유 계층 | 검증된 native/download cache 연결, 방식별 진단 | 단독/복합 의존성 결과 정확, 공유 오류 시 private 복구, 성능·용량 실측 |

핵심 시나리오는 Worktrunk A/B 생성 → 기존 도구 빌드 → 캐시 오류 주입/관리 복구 → A 제거/전용 GC → B 정상 재빌드다. [필수 체크리스트](08-post-implementation-checklist.md)를 Claude/Codex × xcodebuild/XcodeBuildMCP 조합별로 수행한다. GUI는 별도 지원 행으로 검증한다.

기존 PoC는 제품 통합 시험이 아니다. 실제 iOS 실행/XCTest와 cold/warm 반복 시간·피크 용량·실제 회수량을 추가 검증한다. 실패/미실행을 지원 완료로 표시하지 않는다.

## 9. 후속 범위와 남은 검증

자체 seed 저장소, 원격 캐시, 전체 의존성 그래프/상시 입력 감시, 메뉴바, 시뮬레이터 풀, 주기적 GC, 범용 Mac 청소는 후속 범위다. 빌드 실행·워크트리 관리·에이전트 세션 관리는 제품 책임에 넣지 않는다.

구현 전 확정할 것은 지원 버전 조합, 실제 경로 설정/복구 방식, GUI 적용 범위, 외부 사용 종료를 인정할 근거, 보존 기본값이다. 이 중 외부 사용 종료 근거가 불충분한 조합은 자동 GC 대신 후보 표시/명시적 정리만 지원한다. 조건은 숨기지 않고 [검토 기록](09-plan-review.md)에 유지한다.

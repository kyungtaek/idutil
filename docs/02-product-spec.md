# 제품 스펙 — Worktrunk용 빌드 캐시·DerivedData·GC

2026-11-02 갱신. **미구현 설계다.** [플랜](05-development-plan.md)과 [매뉴얼](06-user-manual.md)을 따른다.

## 1. 경계

Worktrunk가 워크트리를 관리하고 우리 도구는 연결된 캐시/빌드 데이터를 관리한다. Git 조회는 대조 근거로만 사용하며 worktree add/remove·branch 변경을 직접 실행하지 않는다.

빌드·실행·테스트는 Xcode/xcodebuild/기존 에이전트 도구에 맡긴다. Tuist는 필요 없다. 첫 통합 사용 방식은 Worktrunk를 전제로 하고 독립 워크트리 관리자 기능을 만들지 않는다.

## 2. 최소 데이터

| 개체 | 책임 |
| --- | --- |
| Repository binding | 실제 Git common directory, 등록된 iOS 범위, Worktrunk 연동 정보 |
| Worktree binding | Worktrunk/Git에서 확인한 경로·실제 식별 정보와 내부 자원 연결 ID. 별도 작업 수명주기 아님 |
| Resource | private/shared/external 소유권, 경로·종류·보존·검증 metadata |
| Reference/provenance | 현재 참조와 복사 출처 구분. 어떤 코드 공간이 자원을 실제 필요로 하는지 |
| Profile | Xcode/SDK/platform/configuration 등 격리 조건과 확인된 경로 설정 |
| Build context | schema version, worktree ID/세대, profile, 프로젝트/workspace 절대 경로, 전용 derivedDataPath, adapter 설정·관측 상태 |
| Reuse decision | 자원/재사용 방식별 reusable/incompatible/unknown, 내용·출처·환경 근거와 검사 시각 |
| Operation journal | prepare/제거 대기/release/GC의 완료·실패·재시도 기록 |
| Usage evidence | 외부 사용 근거·시점·unknown. 모든 빌드 이력을 아는 것으로 취급 안 함 |

상태와 공통 자원은 워크트리 밖에 둔다. 같은 remote의 별도 clone, 같은 경로에 재생성된 워크트리를 구별한다. 에이전트 세션 DB·활동 marker는 만들지 않는다.

위 항목은 하나의 자원 registry/journal에 담을 논리 필드이며 각각 별도 서비스나 DB를 만든다는 뜻이 아니다. 첫 버전의 기준 범위·상태 이름은 [개발 플랜](05-development-plan.md)이 원본이다.

## 3. 인터페이스

공개 명령은 setup/prepare/status/gc/doctor다. --json과 schema version을 제공하고 결과 stdout/진행 stderr를 구분한다. prepare는 정상 경로를 재사용하며 --fresh로 새 관리 세대를 준비한다. status/doctor는 읽기 전용이다. gc는 계획과 --apply <plan-id> 적용을 분리한다. 설치 스크립트는 setup의 얇은 진입점이다.

hook/에이전트 연동 계약은 다음과 같다. prepare는 공개 명령이고 check-release/release만 내부 동작이다. 복구도 동일 prepare 로직을 사용한다.

| 동작 | 입력/결과 |
| --- | --- |
| prepare | 이미 존재하는 워크트리 경로, 저장소·프로필 → prepared/error와 build context. 캐시 모드(shared/private-only), 복구 이유 별도 반환 |
| check-release | 제거 예정 대상 식별 → 해당 소스 트리 내부의 알려진 참조/보호 위험 검사, pending 기록. 외부 캐시의 사용 불명은 GC 보류 사유이며 그 자체로 제거 차단하지 않음 |
| release | 명시적 제거 대상과 pending 기록 → 실제 부재 확인, 참조 해제, 정책상 전용 GC. 불일치 시 보류 |

최초 등록·재실행·동시 hook은 멱등적으로 처리한다. 준비 완료 전 부분 복사본을 노출하지 않는다. 정상 cache miss와 필수 격리 설정 실패를 구분한다. 복구 중 binding은 GC가 활성 참조로 보수적으로 취급한다.

## 4. Worktrunk 계약

기본 연결은 pre-start → prepare, pre-remove → check-release, post-remove → release다. 기존 hook과의 순서/동시 실행을 검증하며 캐시 준비 전 빌드가 시작되는 구성을 허용하지 않는다. 구체 설정은 지원 버전별 통합 시험 후 확정한다. [공식 hooks](https://worktrunk.dev/hook/)

post-remove는 삭제 대상 경로를 인수로 받는다. 현재 실행 디렉터리를 삭제 대상으로 추론하지 않는다. 기본 background 제거를 고려해 요청 수락과 실제 제거 완료를 구분한다. 실패·늦은 callback·중복 callback은 이력으로 대조한다.

hook은 승인되지 않거나 --no-hooks로 생략될 수 있다. doctor는 설치·승인·적용·마지막 준비 상태를 구분한다. prepare/GC는 Worktrunk/Git 목록, 경로·파일 식별, 볼륨 접근성을 대조해 필요한 기록을 갱신하며 워크트리 자체를 대신 제거하지 않는다.

Worktrunk의 Git/브랜치 보존 정책과 우리 자원 보호 정책은 별개다. 설치 시 Worktrunk의 force/branch 삭제/프로세스 종료 기본 설정을 몰래 바꾸지 않는다. 우리의 추가 검사 결과는 cache/산출물 관점이며 Git 안전 검사를 대체하지 않는다.

## 5. 캐시 준비와 경로

| 자원 | 처리 |
| --- | --- |
| 전용 DerivedData DB/intermediates/products/index | 워크트리·프로필별 안정적 격리. 전체 공유/복제 기본 제외 |
| 패키지 다운로드 캐시 | 공식 도구 방식으로 재사용. 외부 소유 전역 캐시는 GC 제외 |
| Xcode compilation store | 검증된 설정으로 공통 이용. 자체 키/내부 객체 조작 없음 |
| 불변 입력·배포물 seed | 후속 기능. 첫 버전의 자체 복사/저장소 엔진에서 제외 |
| SourcePackages checkout·Pods·scratch | 작업별 격리. 원본 cache와 설치 결과를 구분 |
| 등록된 임시 빌드 산출물·성공 로그 | 전용 경로와 제한된 보존 정책 |

세션/실행 UUID나 모든 build setting 조합으로 새로운 캐시를 만들지 않는다. setup에서 명시한 프로젝트/작업 용도의 프로필을 기준으로 재사용한다. 동시 writer는 기존 도구로 직렬화하거나 명시적 별도 프로필로 분리한다. 공통 compiler cache와 동일 mutable DerivedData는 같은 개념이 아니다.

SCM SPM/local SPM/원격·개발 Pod/prebuilt framework는 단독 또는 조합하여 지원한다. [정합성 상세](03-mixed-dependency-consistency.md). 생성 시 내용 검증이 매 빌드 전후 검사나 미래 입력 불변을 보장하지 않는다.

경로 격리 지원을 완료로 판정하려면 실제 도구가 준비 경로를 사용하는지 검증해야 한다. 이는 prepare의 공간 등록 성공과 별도다. 적용 가능한 workspace 설정/기존 명령의 경로 인수 등을 어댑터별 검증한다. 출력 파일/환경 변수만으로 적용됐다고 주장하지 않는다. 설정 변경 범위를 표시하고 프로젝트·전역 설정을 암묵적으로 덮어쓰지 않는다.

prepare는 경로 binding을 원자적으로 저장한다. `status --json`은 현재 워크트리에 맞는 build context를 반환하며, 에이전트가 기존 도구에 적용할 수 있어야 한다. xcodebuild는 `-derivedDataPath`, XcodeBuildMCP는 검증된 버전의 `derivedDataPath`로 연결한다. 생성 hook의 환경 변수만으로 이미 실행 중인 MCP/에이전트 설정을 변경할 수 있다고 가정하지 않는다.

hook은 경로와 binding을 정상 게시하면 prepared로 종료한다. 그 뒤 에이전트가 도구 설정을 적용·확인하고 기존 도구로 빌드한다. prepared는 실제 도구 설정/사용 검증과 다르다. 도구별 증거는 binding 세대·관측 시각과 함께 별도로 표시하며 근거가 없으면 unknown이다. MCP 메모리는 에이전트가 직접 확인하며 status가 실시간으로 안다고 가정하지 않는다.

정상 binding이 있고 호출 인수/MCP 기본값만 빠졌거나 이전 작업 값이면 기존 binding을 재적용한다. binding 자체 누락·손상/실제 경로 충돌/캐시 오류는 새 관리 DerivedData로 복구한다. 기본 공용 위치로 fallback하지 않는다. 호출별 SYMROOT/OBJROOT 등 충돌도 실행 범위에서 교정·검증하며 불가능하면 오류를 보고한다. 새 경로를 만드는 것으로 도구의 설정 불가까지 해결된다고 가정하지 않는다.

복구 자원도 정상 prepare와 같은 관리 루트·resource ID·소유권·프로필·참조·journal·보존 정책으로 등록한다. 별도 비관리 임시 폴더는 허용하지 않는다. 복구 세대를 원자적으로 게시하고 이후에는 그 경로를 재사용한다. 중복 복구를 합치며 이전 자원은 사용 종료 확인 후 기존 GC 절차로 회수한다. 복구 중 부분 자원도 추적 가능해야 한다. [자동 복구 계약](05-development-plan.md#53-캐시-오류는-관리되는-새-공간으로-자동-복구)

캐시 정합성은 출처·내용·관련 의존성/환경·원본 안정성·참조를 계층별로 확인한다. `incompatible` 또는 `unknown`이면 해당 복사/연결을 제외하며, 관리 fingerprint를 자체 컴파일 캐시 키로 사용하지 않는다. [정합성 판정 계약](03-mixed-dependency-consistency.md#8-정합성-판정-계약)

## 6. 소유권과 GC

참조는 관리 자원 단위다. B의 전용 데이터와 공통 자원 참조는 A 제거와 무관하게 유지한다. B가 A 내부 파일을 직접 가리키면 check-release가 위험을 알리고 차단한다. 첫 버전은 이 관계를 자동 복사·수정하지 않는다.

pre-remove 시에는 모든 참조/데이터를 유지한다. Worktrunk의 다른 검사나 실제 삭제가 실패할 수 있기 때문이다. pending 기록은 타임아웃만으로 해제하지 않고 실제 상태를 대조한다. release는 경로 재사용과 늦은 이벤트를 검사한 뒤 참조를 해제한다.

수동 gc와 post-remove의 정책 GC는 같은 계획·잠금·검증 코드를 사용한다. 후자는 setup에서 승인한 범위만 자동 실행하며 그 외에는 후보로 남긴다. active/unknown/보존 자원은 삭제하지 않는다. 후속 seed 기능을 도입하더라도 같은 보존 규칙을 적용한다.

워크트리 제거 허용과 캐시 GC 허용은 분리한다. 관리 루트의 캐시 사용 불명은 GC만 보류한다. 제거 대상 내부의 직접 참조/보호 자료는 제거가 손실을 일으킬 수 있으므로 check-release에서 구분한다. 첫 버전은 자체 관리 전용 데이터 회수가 중심이며 전역/native 공통 저장소를 포함한 전체 디스크 용량 상한은 보장하지 않는다.

최종 전달물·archive/dSYM·미해결 실패 증거·미분류 데이터는 보호한다. 코드·branch, 외부 전역 cache, Xcode CAS 내부 객체는 GC에서 제외한다. 보호 자료가 worktree 안에 있으면 제거 hook에서 알리며, 무조건 백업되거나 복구된다고 약속하지 않는다.

DerivedData 안의 등록된 보호 자료도 같은 규칙이다. 제외 가능한 단위로 정리하고 보호 자료를 분리할 수 없으면 전체 자원을 보류한다. 폴더 이름만 보고 내부 실패 증거까지 삭제하지 않는다.

정리는 소유권·객체 식별·실제 사용을 재확인한다. symlink 탈출·볼륨 미연결·권한 오류·부분 삭제·crash를 처리하고 논리 크기/추정 회수량/실제 여유 변화는 분리한다. 휴지통 이동만으로 공간이 회수됐다고 하지 않는다.

## 7. 외부 빌드 사용

우리 잠금은 Xcode writer를 차단하지 않는다. 사용 근거가 불충분하면 준비 중인 mutable 원본 복사와 자원 삭제를 보류한다. 프로세스를 임의 종료하거나 빌드 실행을 감싸지 않는다.

신뢰할 수 있는 기존 도구 알림이 있으면 보조적으로 사용한다. 미협조 writer와 완전한 동시성 보장을 주장하지 않고, 확인 가능한 정리 구간 또는 검증된 연동에서만 제거한다. Worktrunk의 에이전트 idle/marker 부재도 삭제 근거가 아니다.

## 8. 상태 조회와 에이전트

워크트리 위치·Git 상태·활동은 Worktrunk에서 본다. status는 자원 중심으로 연결 경로, 전용/공유 용량, 참조 수, 준비/설정/GC 상태를 표시한다. 측정 시점과 unknown/partial을 표시하며 전체 디스크 상시 스캔·자체 watch UI를 추가하지 않는다.

설치 스크립트는 Worktrunk 사용 skill/안내와 활성 iOS 프로젝트의 캐시 hook을 연결한다. 기존 Worktrunk 플러그인을 감지·재사용하고 동일 이벤트를 중복 등록하지 않는다. [설치 설계](07-agent-integration.md)

## 9. 검증

A/B 생성, 기존 도구 빌드, Worktrunk A 제거, 전용 GC, B 정상 재빌드를 검증한다. 독립/복합 의존성, binary 교체, GUI/CLI 실제 경로, hook 누락·미승인·중복·순서·부분 실패·외부 제거·같은 경로 재사용도 포함한다.

기존 PoC는 참고이며 제품 통합 시험을 대신하지 않는다. 기존 iOS compile/link 외 실제 실행/XCTest와 반복 성능·회수량 측정이 필요하다. 모든 시험은 구현 시작 승인 이후 수행한다.

xcodebuild/XcodeBuildMCP별 실제 경로와 공용 위치로의 누출은 [구현 후 필수 체크리스트](08-post-implementation-checklist.md)로 별도 확인한다. 설치/설정 존재만으로 통과 처리하지 않는다.

# 의존성 방식별·복합 환경의 캐시 정합성 설계

대상: **SCM 기반 SPM, local SPM module, CocoaPods, static framework/XCFramework를 각각 단독 또는 조합해서 사용하는 iOS 프로젝트**. 네 방식을 모두 사용해야 한다는 AND 조건이 아니다. 작성일 2026-11-02.

기본 대상은 Tuist를 사용하지 않는 기존 `.xcodeproj`·`.xcworkspace`다. Tuist 설치·계정·설정은 요구하지 않는다. 최신 범위는 [Worktrunk 연동 캐시 플랜](05-development-plan.md)을 따른다. 워크트리 관리는 Worktrunk, 우리 도구는 캐시·DerivedData 격리·GC, 빌드는 기존 Xcode 도구가 맡는다. 아래 검증은 prepare/check-release/release와 GC 경계에 적용하며 매 빌드 전후 검사를 강제하지 않는다.

핵심 결정은 ‘공유 폴더 만들기’가 아니라 **어떤 입력으로 만든 결과인지 확인하고, 변경 가능한 상태를 공유하지 않는 것**이다. 본 문서의 엔진 정책은 제안이다. 별도 [샘플](../examples/mixed-dependencies/README.md)은 Xcode 동작을 검증하며 제품 엔진이 이미 구현됐음을 의미하지 않는다.

**전체 검토 후 범위:** 첫 버전은 공식 다운로드/native compilation cache 연결, private 상태 격리와 복구를 구현 대상으로 한다. 아래 자체 seed 복사·게시 프로토콜은 후속 설계다. 모든 의존성 그래프를 구축하거나 모든 파일을 매 빌드 hash하는 엔진은 만들지 않는다. 각 방식의 지원은 기존 패키지/빌드 도구를 그대로 이용하며 격리·결과를 검증한다는 의미다.

## 1. 세 계층을 분리한다

| 계층 | 공유 범위 | 유효성 책임 |
| --- | --- | --- |
| 다운로드된 원본 | 공식 패키지 캐시 또는 확인된 불변 원본 | revision/checksum 및 패키지 관리 도구 |
| 컴파일 결과 | 검증한 Xcode 네이티브 compilation store | 컴파일러가 만든 입력 기반 키와 도구의 저장소 동시성 |
| 변경 가능한 작업 상태 | 워크트리별 DerivedData, Pods, SourcePackages, `.build` 격리 | 생성 시 경로 분리, 외부 빌드 도구의 동시성 관리, 정리 시 사용 확인 |

MVP에서는 자체적으로 `.o`, `.swiftmodule`, `Build/Products`, build DB를 검색해 다른 워크트리에 끼워 넣지 않는다. 완성된 모듈 바이너리의 자체 공유는 별도 검증을 거친 후속 기능이다.

준비한 용량과 실제 캐시 적중은 다르다. 외부 빌드 결과를 검증할 때 다운로드 재사용, compiler cache 적중, 증분 작업 생략을 구분한다. 관측 근거가 없으면 적중률을 알 수 없음으로 둔다.

## 2. 방식별 캐싱 전략과 조합 규칙

아래는 구현할 전략이다. 선택한 workspace·scheme·target에 해당하는 의존성 정보와 프로젝트가 선언한 입력을 기준으로 필요한 전략만 적용한다. 첫 버전에서 모든 전이 그래프·스크립트를 자동 추론하는 엔진은 만들지 않는다. 입력을 확정하지 못하면 진단하고 해당 재사용을 제한한다. 저장소에 Podfile이 존재한다는 이유만으로 모든 빌드에 CocoaPods를 요구하지 않는다. 미사용 도구·lockfile 부재는 오류가 아니지만 필요한 의존성의 해석 실패는 숨기지 않는다.

| 방식/출처 | 재사용할 계층 | 작업별 격리와 무효화 |
| --- | --- | --- |
| SCM SPM | 패키지 도구의 다운로드/저장소 캐시. 컴파일은 검증된 Xcode compilation store 이용 | revision·manifest·전이 의존성·빌드 환경을 확인하고 checkout/SourcePackages/scratch는 작업별 유지 |
| local SPM | 다운로드 캐시는 해당 없음. 소스 컴파일은 검증된 네이티브 캐시와 동일 작업의 증분 상태 이용 | 소스·리소스·manifest·미커밋 변경의 내용 확인. scratch/생성물 격리. 외부 공용 경로는 snapshot/잠금 필요 |
| 원격 소스 CocoaPods | CocoaPods 자체 다운로드 캐시. 컴파일은 해당 언어·툴체인에서 지원/검증한 범위만 재사용 | lock 기반 설치, Pods·생성 프로젝트 격리. podspec·설정·스크립트 입력·도구 버전까지 확인 |
| local/development Pod | 다운로드 재사용 없음. 실제 소스의 증분 빌드와 검증된 컴파일 캐시 이용 | Podfile.lock이 같아도 실제 소스·리소스·podspec 내용을 확인. 설치/생성 상태는 작업별 격리 |
| prebuilt static framework/XCFramework | 기존 배포 도구의 원본 캐시. 자체 독립/CoW 배포물 복사 엔진은 후속 | binary·header·modulemap·interface·resource·선택 slice를 진단·시험. 소비자 컴파일·링크·서명 결과는 작업별 격리 |

prebuilt 바이너리에는 이미 컴파일된 라이브러리 자체의 재컴파일 절감을 약속하지 않는다. 원본 다운로드/배치 비용, 소비자 컴파일, 링크 비용을 구분한다. 프로젝트 안에서 소스로 빌드하는 static framework target은 소스 컴파일 전략을 적용한다. CocoaPods 사용만으로 모든 C/ObjC 작업의 캐시 적중을 보장하지 않는다.

**배포 방식과 산출물 형식은 별도 축이다.** XCFramework는 SPM, CocoaPods 또는 직접 vendoring으로 들어올 수 있다. 이 경우 배포 방식의 원본 캐시 정책과 바이너리 내용 검증을 함께 적용한다. 이름이 같다는 이유로 다른 출처의 의존성을 합치지 않으며, 공유 파일의 용량과 참조 수는 중복 계산하지 않는다.

복합 환경에서는 확인된 입력과 전이 의존성 정보로 초기 복사/연결 가능 여부를 판단한다. 완전한 영향 그래프를 자체 계산하지 않는다. 불확실하면 해당 항목을 이식하지 않고 새 워크트리의 private state에서 기존 도구가 만들게 한다. 이미 사용 중인 다른 작업의 캐시는 변경·삭제하지 않는다. 생성 후 증분 무효화는 기존 빌드 시스템의 책임이다.

첫 버전의 검사 비용도 계층별로 제한한다. 공식 원본 캐시의 객체 검증은 기존 도구에 맡기고, 자체 입력 digest는 등록된 local/vendored 대상의 prepare 재검증·명시 진단에서 사용한다. 전체 그래프가 unknown이어도 격리된 정상 빌드를 막지 않는다. 외부 경로의 local package/개발 Pod는 자동 snapshot하지 않으며 공용 소스가 쓰이는 범위를 진단한다.

### 식별과 무효화 입력

| 의존성 | 기본 식별 정보 | 추가로 확인할 입력 | 공유 금지 작업 상태 |
| --- | --- | --- | --- |
| SCM 기반 SPM | 정규화된 원격 주소, 실제 commit revision, Package.resolved | Package.swift, 선택 product/target, tools version, 전이 의존성, plugin/macro, registry 설정 | checkout, SourcePackages 작업 디렉터리, `.build` |
| local SPM | 실제 경로/소유권 + 입력 파일 내용 digest | 소스·리소스·manifest·전이 local dependency·빌드 plugin과 생성 입력, uncommitted 변경 | 해당 package의 scratch와 생성물 |
| CocoaPods | Podfile, Podfile.lock, Gemfile.lock, CocoaPods 버전 | 실제 소스·podspec·개발 pod 경로, vendored binary, xcconfig, script phases, static/dynamic linkage | Pods/Manifest.lock을 포함한 설치 결과와 생성 프로젝트 |
| static framework/XCFramework | 배포물 전체 내용 digest와 선택 slice | 실제 archive/Mach-O, headers, modulemap, swiftinterface/swiftmodule, resource bundle, 전이 link 입력 | 링크 결과·서명 결과·빌드 intermediates |

`Package.resolved`는 local package의 모든 수정 내용을 표현하지 않는다. `Podfile.lock`도 개발 pod의 소스 파일 전체 hash가 아니다. 같은 이름·버전의 바이너리가 교체될 수도 있다. 따라서 잠금 파일 두 개를 합친 hash만으로 정합성을 보장할 수 없다.

CocoaPods의 기존 설치 흐름은 lockfile 기반 `pod install`을 유지하며 변경 금지에는 `--deployment`를 사용할 수 있다. CLI는 설치 결과를 복사했다고 설치 검증 완료로 보지 않으며 자동 `pod update`로 수정하지 않는다. 필요한 준비는 기존 패키지 도구의 작업으로 안내한다. [공식 설치/업데이트 구분](https://guides.cocoapods.org/using/pod-install-vs-update.html), [명령 옵션](https://guides.cocoapods.org/terminal/commands.html)

원격 SPM binary target의 배포 archive에는 checksum을 검증할 수 있지만 local/path 기반 binary는 같은 보호를 자동으로 얻는다고 가정하지 않는다. 유틸이 로컬 배포물 내용 digest를 기록한다. [Apple binary package 문서](https://developer.apple.com/documentation/xcode/distributing-binary-frameworks-as-swift-packages)

## 3. 관리용 fingerprint와 컴파일 캐시 키를 구분한다

관리용 fingerprint는 다음 세 부분을 기록한다.

```text
environment = Xcode build version + Swift/Clang toolchain + SDK build
            + platform/variant + architectures + deployment target
            + effective build settings + relevant environment

dependencyGraph = resolved revisions + manifests + local content digests
                + actual binary slice digests + transitive dependencies
                + link mode + module/product identity

generation = generator/plugin/macro version and executable digest
           + declared inputs + configuration + output manifest
```

관찰하는 설정에는 compilation conditions, optimization, Swift language version, sanitizer, coverage, module stability/library evolution, C/ObjC defines, header/module search path, linking flags와 resource processing이 포함된다.

**이 목록을 직접 조합해 만든 hash를 Xcode 컴파일 결과의 완전한 캐시 키로 사용하지 않는다.** 엔진은 이를 후보 선택·초기 배치·호환성 진단에 사용한다. 실제 빌드 입력을 확인하지 못한 부분은 unknown이며 개별 컴파일 재사용 판단은 Xcode에 맡긴다.

의존성 이름 충돌, SPM/Pods로 같은 모듈을 이중 도입, static framework의 중복 심볼·누락된 전이 링크 플래그는 사전 진단 대상이다. 캐시 초기화로 이러한 구성 오류를 숨기지 않는다.

## 4. 캐시 준비와 제거 경계의 프로토콜

첫 버전은 공식 캐시 연결과 전용 경로 등록을 수행한다. 아래 복사/seed 관련 절차는 후속 기능에만 적용하며 생성 hook에서 전체 빌드 그래프나 파일 트리를 복사하지 않는다.

1. **후보 발견:** 등록된 기존 워크트리와 캐시의 소유권·출처·원본 경로를 확인한다.
2. **입력 확인:** 대상 코드의 revision/lock과 local/vendored 실제 내용을 비교한다. dirty 입력을 commit ID만으로 대체하지 않는다.
3. **계층별 판정:** native cache store는 도구가 지원하는 연결 조건을, 복사 seed는 내용·형식·사용 상태를 검증한다. 호환 불명인 mutable 상태는 이식하지 않는다.
4. **일관된 준비:** 사용 중인 원본 복사를 피하고, 임시 사본에서 내용/metadata를 확인한 뒤 완성된 항목만 게시한다. 다른 작업 내부 경로에 종속되는 연결을 만들지 않는다.
5. **설정/참조 기록:** 기존 Xcode 도구가 사용할 경로, 적용한 설정, 추가 설정 필요 여부와 참조 관계를 기록한다. 이 단계에서 빌드는 실행하지 않는다.
6. **제거 전 점검:** check-release는 대상 내부의 알려진 참조·사용·보호 산출물 위험을 검사한다. 외부 캐시 사용 불명은 GC만 보류한다. 원본이 제거 대상 안에 있어 직접 손실이 생기는 경우는 hook을 차단하며 첫 버전에 자동 복사/참조 수정은 제공하지 않는다. 코드/브랜치 보존 정책은 Worktrunk가 담당한다. 캐시 삭제/참조 해제는 아직 하지 않는다.
7. **실제 제거 후 해제:** release가 Git/실제 경로를 대조해 제거 완료를 확인한 뒤 참조를 해제하고 정책 GC를 수행한다. 다른 hook/제거 단계 실패, 늦은 callback, 같은 경로 재사용은 완료로 오인하지 않는다. 우리 도구는 워크트리 자체를 삭제하지 않는다.

복사 전후 hash 비교만으로 중간 변경 후 원복까지 모두 검출하지 못한다. 파일별 CoW 복사도 전체 디렉터리의 원자적 snapshot은 아니다. 불변 입력 또는 쓰기가 멈춘 검증 가능한 원본을 사용하고 그렇지 않으면 준비를 건너뛴다.

CLI 잠금에 외부 Xcode writer가 참여한다고 가정하지 않는다. 생성/정리 시 관측 가능한 사용 근거를 확인하되 불확실하면 보류한다. 최초 준비 후 사용자가 파일을 바꾼 모든 순간까지 이 CLI가 검사·재빌드하는 것은 아니다. 선택적 외부 lifecycle 연동은 빌드 실행을 소유하지 않고 근거를 보완하는 수단이다.

파일시스템 활동과 mtime는 보조 신호다. 복사/게시 판단은 실제 내용과 출처 검증을 사용한다. 기존 PoC의 동일 크기/mtime 바이너리 교체 같은 빌드 시스템 한계는 자동 해결된 것으로 표현하지 않는다.

## 5. 검증된 초기 복사본을 보존하는 경우 — 후속 설계

원본/배포물의 제한된 seed 보존이며 자체 컴파일 결과 캐시나 Xcode CAS 수정 기능이 아니다. 첫 버전에는 넣지 않으며 도입 시 최초 허용 형식을 별도 검증한다. 아래 프로토콜은 현재 출시 통과 조건이 아니다.

- 원본/배포물을 임시 디렉터리에 준비한다.
- 전체 tree digest와 slice metadata를 확인한다.
- 대상별 lock을 획득하고 완성된 항목만 atomic publish한다.
- 내용 digest로 주소를 정하며 기존 항목을 제자리에서 바꾸지 않는다.
- 워크트리에는 독립 복사/CoW 복사하거나 검증된 읽기 전용 원본으로 참조한다.
- 수동으로 덮어쓴 항목은 다음 검증에서 격리한다. 파일 권한만으로 변조가 불가능하다고 보장하지 않는다.
- 소스·중간 산출물에는 writable hardlink/symlink 공유를 하지 않는다.

공식 SPM/CocoaPods 캐시와 Xcode compilation cache를 우선 이용한다. 검증된 seed가 다른 작업에 참조되면 원본 워크트리가 없어져도 유지한다. 참조가 없어도 보존 기간·용량 정책이 유효하면 남기며, 외부 소유 캐시와 CAS 내부 객체는 자체 gc에서 제외한다. 자체 다운로드 mirror·패키지 설치 결과 캐시는 초기 기본값이 아니다.

## 6. 정합성을 입증해야 할 시험 행렬

먼저 SCM SPM만, local SPM만, CocoaPods만(원격/개발 Pod 각각), 직접 prebuilt binary만 사용하는 구성을 검증한다. 이어 SPM+Pods, local SPM+binary, SPM/Pods를 통한 binary 배포 등 대표 조합과 전이 의존성을 검증한다. 같은 모듈의 이중 도입과 한 의존성 변경이 다른 작업에 섞이지 않는지도 확인한다. 필요한 도구만 요구하고 사용하지 않는 방식은 설치하지 않아도 되어야 한다.

기존 네 방식 혼합 샘플은 회귀 시나리오 하나다. 모든 방식의 동시 사용을 지원 조건으로 삼거나, 이 샘플만으로 각 방식과 모든 조합이 검증됐다고 선언하지 않는다. 다음 변경 시험은 관련 단독 구성과 대표 조합에 각각 적용한다.

| 변경/조건 | 기대 결과 |
| --- | --- |
| 동일 commit, 다른 워크트리 경로 | 가능한 항목만 재사용, 결과 동일 |
| Xcode GUI/기존 CLI로 준비된 공간 빌드 | 설정 적용과 실제 사용 경로를 확인. 캐시 준비와 hit를 구분 |
| A에서 B 준비 후 A 제거 | B의 독립 사본/공통 참조 유지, 기존 도구로 B 재빌드 정상 |
| 다른 작업이 A 내부 자원을 직접 참조 | A 제거 전 위험 표시/차단. 자동 복사·재연결은 첫 버전 범위 아님 |
| 원본 빌드 진행 중 Worktrunk 생성/제거 hook | 불안정한 복사/삭제 보류, 정상 원본 보존 |
| pre-remove 통과 뒤 Worktrunk 제거 실패 | 캐시와 참조 유지, pending 상태 재확인 |
| post-remove 누락/중복/지연 또는 같은 경로 재사용 | GC 대조로 실제 객체 확인, 다른 작업 자원 비삭제 |
| local SPM의 dirty 소스 변경 | 해당 코드가 새 값 반환 |
| local SPM resource만 변경 | 리소스 bundle에 새 내용 반영 |
| Pod 소스만 변경, lock 동일 | 새 소스 반영 |
| Podfile/lock/linkage 변경 | 설치 상태 재검증, 의존 대상 재빌드 |
| static binary 동일 이름·버전 교체 | 새 binary slice 링크 |
| static binary 동일 크기·mtime로 교체 | 내용 변경 감지 또는 안전한 재빌드 |
| header/modulemap/swiftinterface만 변경 | 소비자 컴파일과 ABI 호환성 재검증 |
| SCM revision 변경 | 정확한 revision 사용, 이전 코드 미혼입 |
| SDK/architecture/Debug-Release 변경 | 부적합 산출물 재사용 금지 |
| 동시 빌드 A/B가 서로 다른 의존성 사용 | 각 실행값과 리소스가 독립적으로 정확 |
| 취소/강제 종료/캐시 부분 쓰기 | 불완전 항목 노출 방지. 복구 시 유효한 전용 DerivedData에서 cold build, 기본 공용 경로 fallback 금지 |
| plugin/script의 미선언 입력 변경 | 불확실한 작업의 공유 재사용 배제 |
| 캐시 없이 새 DerivedData로 빌드 | 캐시 사용 결과와 기능적으로 동일 |

## 7. 제한과 도입 기준

Xcode의 캐시도 버전별 결함 가능성이 있다. Swift 저장소에는 prefix mapping과 캐시 진단 재생 조합의 Xcode 27.0 crash 보고가 있다. 이 샘플에서 재현한 결함이라는 뜻은 아니다. 따라서 prefix mapping을 프로젝트 전체에 무조건 켜지 않고, 해당 툴체인·패키지·커버리지 조합으로 검증한 설정만 사용한다. [Swift 이슈 #92545](https://github.com/swiftlang/swift/issues/92545)

정상 빌드 몇 번이 통과했다는 이유로 모든 혼합 프로젝트에 대해 안전하다고 선언하지 않는다. 샘플 검증 뒤 실제 대표 프로젝트 하나에서 생성 코드, Swift 정적 프레임워크, resource bundle, signing, 테스트를 포함한 비교를 통과해야 자동 공유를 기본 활성화한다.

속도보다 정합성을 우선한다. 특정 유형을 안전하게 준비할 수 없으면 이식하지 않고 기존 도구의 정상 생성 경로를 사용한다. 생성 후 변경 시험은 외부 빌드 도구와의 통합 시험이며 CLI가 매 빌드의 정확성을 직접 강제한다는 보증이 아니다. 결함이 확인되면 해당 이식 방식을 미지원으로 남기고 다른 검증된 캐시는 유지한다.

## 8. 정합성 판정 계약

다음은 구현할 판정 기준이다. 기존 샘플의 통과 결과를 전체 지원 범위의 보증으로 사용하지 않는다.

### 8.1. 무엇을 판정하는가

| 대상/행위 | 승인에 필요한 근거 | 승인 범위 |
| --- | --- | --- |
| 공식 다운로드 캐시 이용 | 출처와 실제 revision/checksum을 해당 패키지 도구가 검증하는 지원 경로 | 원본 재사용. 설치/컴파일 결과까지 유효하다는 뜻은 아님 |
| 자체 보존 불변 seed 복사/연결(후속) | 출처, 전체 내용 digest, 필요한 형식/slice, 대상 요청과의 일치, 일관된 게시 | 도입 시 검증된 입력/배포물의 배치만 승인 |
| Xcode compilation store 연결 | 지원 Xcode/툴체인 조합·설정·동시성 시험, store 소유권/namespace 정책 | store 연결만 승인. 개별 입력 기반 hit/miss는 Xcode 책임 |
| 기존 워크트리의 private DerivedData 계속 이용 | 동일 worktree ID/세대·프로필과 경로, 확인된 설정. 알려진 입력 변경 위험 진단 | 기존 빌드 시스템의 증분 처리에 맡김. 개별 산출물 유효성 보증 아님 |
| 다른 워크트리의 mutable DerivedData/Pods 설치 결과 이식 | 첫 버전에서는 승인하지 않음 | lockfile/commit이 같아도 제외 |

다운로드 원본은 Debug/Release가 다르다고 불일치 처리하지 않는다. 바이너리는 선택 platform/variant/architecture 및 소비자가 요구하는 형식 호환성을 확인한다. 도구가 검증하는 compiler key를 우리 관리 hash로 대체하지 않는다.

### 8.2. 내용 확인 방법

자체 seed 관련 항목은 후속 기능의 기준이다. 첫 버전의 local/vendored 내용 진단과 공식 캐시 연결 정책에 필요한 항목만 적용한다.

- 자체 seed는 버전이 있는 manifest와 SHA-256 기반 tree digest를 사용한다. 상대 경로·파일 종류·필요한 실행 권한·파일 내용·symlink 대상을 결정적인 순서로 포함한다. 예상하지 못한 외부 경로 참조는 승인하지 않는다.
- local SPM/development Pod의 fingerprint는 실제 입력 소스·리소스·manifest/podspec와 선언된 생성 입력을 포함한다. dirty/untracked 입력도 빌드 대상이면 포함하며 commit ID로 대체하지 않는다.
- prebuilt 배포물은 binary뿐 아니라 headers/modulemap/Swift interface/resource와 slice metadata를 포함한다. 이름·버전·파일 크기·mtime만 같은 것은 통과 근거가 아니다.
- 제외 항목은 명시적 정책으로 관리한다. 입력인지 모르는 파일을 단순 캐시/생성물로 추정해 제외하지 않는다. plugin/script가 외부 환경·네트워크·선언되지 않은 파일에 의존하면 관련 자체 재사용은 unknown이다.
- 불변으로 게시한 자원도 복사/연결 전에 manifest와 실제 내용을 검증한다. 캐시된 digest의 mtime/size만 보고 내용 검증을 생략하지 않는다. 원본이 바뀌는 중이면 복사 전후 hash가 같아도 일관된 snapshot으로 승인하지 않는다.

### 8.3. 판정과 조치

| 결과 | 의미 | 조치 |
| --- | --- | --- |
| reusable | 그 자원·행위에 필요한 근거가 모두 확인됨 | 해당 재사용만 허용하고 참조/근거 기록 |
| incompatible | revision/내용/환경/형식 등 구체적 불일치 | 대상용 새 자원을 준비. 다른 작업의 정상 자원은 유지 |
| unknown | 입력 누락, 미지원 버전, 사용 중 원본, 검증 실패 등 근거 부족 | 해당 재사용을 생략하고 전용 공간에서 빌드. 경로/캐시 충돌은 새 관리 DerivedData로 복구. 추측으로 재사용 승인하지 않음 |

기록에는 resource ID, 재사용 방식, 기대/관측 fingerprint, 불일치 항목, 도구/정책 버전, 확인 시각, 원본 안정성 근거, 판정 이유를 담는다. `reusable`은 compiler hit 표시가 아니다. 정상적인 캐시 부재도 손상과 구분한다.

내용이 바뀌면 기존 immutable seed를 덮어쓰지 않고 새 ID로 만든다. 손상된 자체 seed는 신규 참조를 차단하고 영향을 받는 참조를 알린다. 사용 중인 원본/사본을 자동 삭제하지 않는다. 공유 store를 통째로 초기화하거나 Xcode CAS 내부 항목을 직접 수정하지 않는다.

### 8.4. 언제 다시 확인하는가

prepare/명시적 재준비에서 해당 계층의 판정을 수행한다. 후속 seed 기능에는 복사·게시·연결 시 검사를 추가한다. 도구·SDK·프로필·정책이 바뀌면 관련 호환성 근거를 재검토하되 그 자체로 모든 private 경로를 바꾸지는 않는다. doctor의 명시적 검증은 현재 근거를 재수집하고, 일반 status는 관측 시점이 있는 결과를 보여준다. GC 시에는 별도로 실제 소유권·사용 상태·참조를 다시 확인한다.

에이전트의 매 빌드 전 확인은 **현재 worktree와 유효 DerivedData 경로**의 대조다. 매 빌드마다 전체 의존성을 hash하거나 Xcode의 무효화를 대체하는 기능은 아니다. 따라서 최초 prepare의 정합성 통과가 이후 임의 입력 변경까지 보증하지는 않는다.

동일 크기/mtime 바이너리 교체 등 알려진 증분 오류는 구현 후 변경 시험에서 반드시 다룬다. 감지된 캐시 오류는 새 관리 DerivedData로 복구하고 의심되는 공유 연결을 제외해 기존 도구가 클린 빌드하게 한다. 새 자원에도 같은 내용·소유권·참조·보존/GC 규칙을 적용하며, 비관리 예외 경로를 만들지 않는다. 이전 자원은 사용 종료 전 삭제하지 않는다. 기존 Xcode 도구로 복구한 결과까지 확인해야 해결로 기록한다. 임의 입력 변경에 따른 모든 stale 결과를 자동 감지한다는 보증은 아니며, 미감지 위험과 지원 제한은 계속 명시한다.

### 8.5. 제품 검증의 정합성 통과 기준

캐시를 사용하는 빌드와 격리된 cold 기준 빌드를 비교해 의존성 revision·테스트 반환값·리소스 내용·선택 binary slice·링크 결과가 의도대로 같아야 한다. 서명/시각/UUID 등 비결정적 metadata 때문에 앱 전체 byte hash의 동일함을 일률적으로 요구하지 않는다. 관련 단독 구성과 대표 복합 구성에서 입력 변경이 새 결과로 반영되고, A/B 동시 작업에 서로의 값이 섞이지 않아야 한다.

빌드 exit code 0, 파일 생성, 속도 향상만으로 통과하지 않는다. 정합성 실패가 남은 자원/버전 조합은 자동 공유 지원 목록에서 제외한다. 시험별 기대값·실측값·근거·제한은 [구현 후 체크리스트](08-post-implementation-checklist.md)에 기록한다.

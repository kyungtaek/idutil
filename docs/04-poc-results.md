# 혼합 의존성 캐시 PoC 결과

실행일: 2026-11-02, Asia/Seoul.

**결론: 작업별 DerivedData와 공유 compilation store를 함께 사용할 수 있는 가능성을 확인했다. 하지만 lockfile·파일 이름·mtime만 믿으면 빌드가 성공해도 이전 바이너리가 남는 경우를 재현했다. 내용 검증과 전용 빌드 상태 무효화가 필요하다.**

제품의 정합성 보증이나 전체 엔진 구현 완료를 의미하지 않는다.

## 환경과 구성

- macOS 27.0.1, Apple Silicon.
- Xcode 27.0 (27A266a), XcodeGen 2.46.0, CocoaPods 1.16.2.
- 두 Git worktree, 각자의 DerivedData/SourcePackages/Pods.
- 실험 전용 `COMPILATION_CACHE_CAS_PATH`와 `-packageCachePath` 공유.
- SCM SPM fixture는 별도 로컬 Git remote의 revision으로 고정.
- local SPM은 Swift 소스 및 resource bundle 포함.
- CocoaPods는 Objective-C 개발 pod, static framework 방식.
- static XCFramework는 C 정적 프레임워크의 macOS/iOS Simulator/iOS arm64 slice.
- 동일 의존성을 사용하는 macOS 실행 probe와 iOS 앱.

실험의 워크트리·빌드 데이터·공유 캐시는 전용 `.lab` 안에 만들었다. 기존 전역 캐시를 삭제하거나 다른 프로젝트의 DerivedData를 재사용하지 않았다. `DEVELOPER_DIR`는 해당 프로세스에만 지정했다.

## 결과

총 16개 관찰 중 15개에서 기대 결과를 얻었다. 14번에서 의도적으로 만든 불리한 조건으로 stale output을 재현했고, 15/16번 대조·대응 실험에서 새 값을 확인했다. ‘16개 테스트 전부 통과’로 보고하면 안 된다.

| 번호 | 조건 | 결과 | 소요 초 |
| --- | --- | --- | ---: |
| 01 | A 첫 빌드 | 실행값 일치 | 9.044 |
| 02 | A 변경 없는 재빌드 | 실행값 일치 | 1.016 |
| 03 | 다른 경로 B, 같은 입력 | 실행값 일치 | 5.144 |
| 04 | B local SPM 소스 변경 | 새 코드 반영 | 1.511 |
| 05 | B local SPM resource 변경 | 새 리소스 반영 | 1.017 |
| 06 | B 개발 Pod 소스 변경 | 새 코드 반영 | 1.260 |
| 07 | B 같은 버전의 static binary 교체 | 새 바이너리 반영 | 1.909 |
| 08 | B SCM SPM revision 변경 | 새 revision 반영 | 1.618 |
| 09 | A/B 동시 빌드의 A | A 고유 실행값 일치 | 1.512 |
| 10 | A/B 동시 빌드의 B | B 고유 실행값 일치 | 1.501 |
| 11 | B 새 DerivedData, 캐시 비활성 | 캐시 사용 결과와 실행값 일치 | 7.666 |
| 12 | A iOS Simulator arm64 | compile/link 성공, 실행 미검증 | 5.756 |
| 13 | B iOS Simulator arm64 | compile/link 성공, 실행 미검증 | 3.518 |
| 14 | binary만 내용 변경, 크기·mtime 유지 | **빌드 성공했지만 이전 값 사용** | 0.970 |
| 15 | 14번 입력, 새 DerivedData, 캐시 비활성 | 새 바이너리 값 확인 | 7.917 |
| 16 | 14번 입력, 내용 hash에 따른 새 전용 빌드 경로, 공유 CAS 유지 | 새 바이너리 값 확인 | 6.328 |

16번은 같은 실험 데이터에 후속 명령으로 실행했으며 재실행 스크립트에도 동일 단계를 포함했다. 1회 소규모 실험이므로 시간 차이를 제품 성능 향상률로 일반화하지 않는다.

## 관찰 1: lockfile은 충분하지 않다

04~07번에서 `Package.resolved`와 `Podfile.lock`의 hash는 03번과 동일했지만, 의도한 실행값은 각각 바뀌었다. 08번에서는 SCM revision 변경에 따라 Package.resolved가 바뀌었다.

따라서 local SPM의 소스·리소스, 개발 pod 소스, 직접 제공한 static binary를 잠금 파일 밖의 입력으로 추적해야 한다. 버전 문자열이 같은 바이너리도 내용이 바뀔 수 있다.

## 관찰 2: ‘빌드 성공’만으로 정합성을 확인할 수 없다

14번은 macOS slice의 static archive 하나만 변경했다. 파일 경로, 크기, mtime를 유지했고 입력 manifest에서 달라진 파일도 이 archive 하나였다.

```text
기대: remote-v2/local-v4/resource-v2/pod-v2/binary-3
실제: remote-v2/local-v4/resource-v2/pod-v2/binary-2
xcodebuild exit: 0
```

[14번 로그](evidence/mixed-dependencies-2026-11-02/14-b-preserved-binary-mtime.log)에는 빌드 성공이 기록됐지만 새로운 compile/link 작업은 보이지 않는다. 공유 CAS가 잘못된 객체를 반환했다고 증명한 사례가 아니라, **증분 빌드가 변경을 놓친 사례**로 분류한다.

15번은 fresh build로 `binary-3`을 확인했다. 16번도 공유 compilation store를 유지한 채 새로운 private build state를 사용해 `binary-3`을 확인했다. 즉, 이런 경우에 전역 공유 캐시를 모두 삭제하는 대응은 필요하지 않았다.

## 관찰 3: 재사용은 확인됐지만 지표를 구분해야 한다

Xcode가 출력한 cacheable tasks 기준:

- 03번 B의 새로운 경로: 146/161, 91%.
- 13번 B의 iOS compile/link: 125/140, 89%.
- 16번 내용 변경 대응 빌드: 149/161, 93%.

SDK와 module compilation 작업을 포함한 수치다. 앱 소스 자체가 91% 재사용됐다거나 전체 빌드가 91% 빨라졌다는 뜻이 아니다. warm build의 무작업 생략도 compilation cache hit와 따로 봐야 한다.

## 스펙에 반영한 결정

1. DerivedData·Pods·SourcePackages·SwiftPM scratch를 워크트리별로 격리한다.
2. 공식 compilation/download cache 계층만 우선 공유한다.
3. local/vendored 의존성의 내용 digest를 기록한다. lockfile과 mtime는 보조 정보다.
4. 내용 변경을 메타데이터가 설명하지 못하면 해당 작업의 private build state를 무효화한다.
5. 공유 compilation store는 유지하면서 정확한 재빌드가 가능한지 검증한다.
6. cache-enabled 결과와 fresh control의 실행값·resource·link 결과를 비교한다.

이 실험은 내용 검증과 전용 빌드 상태 갱신의 필요성을 보여준다. 최신 [Worktrunk 연동 플랜](05-development-plan.md)은 빌드를 실행하거나 매 빌드 입력 검사를 강제하지 않으므로, 이 대응이 자동 구현됐거나 모든 외부 빌드에서 보장된다고 해석하면 안 된다. 캐시 준비/이식의 회귀 기준과 기존 빌드 시스템의 제한으로 유지한다. 일반 프로젝트의 모든 입력을 자동 추적하는 엔진은 아직 없다.

## 확인하지 않은 범위

- iOS 시뮬레이터에서 실제 앱 실행 및 XCTest. 이번 `simctl list` 조회가 응답하지 않아 해당 조회 프로세스를 종료했고 새 device는 만들지 않았다.
- x86_64, 다른 Xcode/SDK, Release, signing, 실제 디바이스.
- Swift prebuilt framework의 ABI/module stability와 복잡한 전이 의존성.
- CocoaPods 원격 저장소/다운로드 캐시, 원격 SPM registry 또는 네트워크 전송.
- plugin/macro, 임의 생성 스크립트, coverage, debugger source mapping.
- 캐시 손상/동시 GC/프로세스 crash 중 publish 같은 관리 엔진 자체의 검증.

처음에는 generic iOS Simulator가 x86_64까지 요구해 샘플의 arm64-only binary와 충돌했다. 샘플의 검증 대상을 arm64로 명시한 뒤 위 실험을 실행했다. 초기 실패 로그도 보존했다.

## 재현 자료

- [샘플 및 실행법](../examples/mixed-dependencies/README.md)
- [실험 스크립트](../examples/mixed-dependencies/run_experiment.py)
- [원시 결과 JSON](evidence/mixed-dependencies-2026-11-02/report.json)
- [증거 파일 SHA-256 목록](evidence/mixed-dependencies-2026-11-02/sha256.json)
- [초기 아키텍처 실패 기록](evidence/mixed-dependencies-2026-11-02/initial-ios-architecture-failure.log)

JSON/로그의 `.lab` 절대 경로는 실행 당시 경로다. 재실행 시 새 실험 경로를 생성한다.

검증 후 증거 26개 파일의 hash를 확인하고 이번 실행의 임시 `.lab` 두 디렉터리를 제거했다. 정리 전 `du` 기준 약 3.1 GiB의 실험 데이터였다. 논리적인 삭제 크기이며 APFS의 실제 회수량 측정치는 아니다. 현재 보존 자료는 문서·로그 약 3 MB, 샘플 약 88 KB, 재실행용 격리 Ruby 의존성 약 23 MB다.

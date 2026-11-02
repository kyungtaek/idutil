# Mixed dependency cache experiment

iOS Disk Util의 공유 캐시 정합성을 검토하기 위한 작은 실행 가능한 샘플이다. 메뉴바 앱이나 최종 관리 엔진 구현은 아니다.

## 구성

- SCM SPM: 별도의 로컬 Git remote에 두 commit을 만들고 revision으로 고정한다. 네트워크 패키지 서버 실험은 아니다.
- local SPM: Swift 코드와 `Bundle.module`로 읽는 리소스.
- CocoaPods: Objective-C 개발 pod, static framework linkage. 원격 pod 다운로드 캐시 실험은 아니다.
- static XCFramework: C 정적 프레임워크의 macOS/iOS Simulator/iOS arm64 slice.
- 소비자: 같은 의존성 코드를 쓰는 iOS 앱과 macOS 명령행 probe.

두 Git worktree는 DerivedData/SourcePackages/Pods를 분리하고, 이 실험 전용 compilation store와 SPM package cache를 공유한다. 전역 DerivedData 경로를 공유하거나 삭제하지 않는다. `DEVELOPER_DIR`를 프로세스에만 지정한다.

## 실행

검증 환경: Xcode 27.0 (27A266a), XcodeGen 2.46.0, Ruby 3.3, CocoaPods 1.16.2, Apple Silicon. iOS와 macOS 모두 arm64로 제한한다.

저장소 루트에서:

```sh
cd examples/mixed-dependencies
BUNDLE_PATH=../../.tools/bundle /opt/homebrew/opt/ruby@3.3/bin/bundle install
cd ../..
python3 examples/mixed-dependencies/run_experiment.py
```

다른 설치 위치는 `DEVELOPER_DIR`와 `IDUTIL_BUNDLE`로 지정한다. XcodeGen은 PATH에서 찾는다. 의존성 도구는 `.tools/bundle`에 설치하며 시스템 gem을 변경하지 않는다.

각 실행은 `.lab/mixed-<timestamp>` 아래에 원격 fixture, seed Git 저장소, A/B worktree, 빌드 데이터, 캐시, 로그와 `report.json`을 만든다. 기존 실험을 덮어쓰거나 자동 삭제하지 않는다. 이 경로는 `LAB=`로 출력된다.

## 검사

1. A cold/warm 및 다른 경로의 B 빌드.
2. local SPM 소스, 리소스, Pod 소스, static binary, SCM revision을 하나씩 변경.
3. 서로 다른 값을 갖는 A/B 동시 빌드와 실행값 확인.
4. 캐시를 끈 새 DerivedData의 대조 빌드.
5. A/B iOS Simulator용 앱 compile/link.
6. static binary의 크기·mtime를 유지한 교체로 stale output 여부 관찰.
7. 새 private build state + 캐시 비활성 대조군, 이어 공유 compilation store를 유지한 내용 변경 대응 빌드.

probe는 다음처럼 각 의존성의 실제 실행 결과를 출력한다.

```text
remote-v2/local-v4/resource-v2/pod-v2/binary-3
```

14번은 의도적으로 취약 조건을 관찰한다. `passed: false`와 `xcodebuild` exit code 0이 함께 나올 수 있으며 이 결과를 숨기지 않는다. 스크립트 exit 0은 관찰 및 대조군 실행을 완료했다는 뜻이지, 모든 경우의 캐시 정합성이 통과했다는 뜻이 아니다.

`xcode_cache_metrics`는 Xcode가 보고한 cacheable tasks 지표다. SDK/module 작업을 포함하며 앱 소스만의 적중률이 아니다. `cache_hit_mentions`는 중복 진단을 포함하므로 적중률 계산에 사용하지 않는다.

## 한계

- 실행값 검증은 macOS에서 수행한다. iOS 앱은 컴파일·링크만 검증한다.
- Swift로 만든 prebuilt framework의 ABI/module stability, 원격 registry, CocoaPods 원격 배포물, plugin/macro, signing은 이 fixture가 검증하지 않는다.
- prefix mapping은 추가 설정하지 않는다. 경로 문제를 감추기 위해 빌드 DB를 복사하지 않는다.
- 작은 샘플의 1회 측정이며 제품 빌드 속도 개선율로 일반화하지 않는다.
- 전체 제품의 자동 무효화 엔진은 미구현이다. 마지막 실험은 대응 정책의 검증이다.

실측 결과와 저장된 증거는 [검증 보고서](../../docs/04-poc-results.md)에 정리한다.

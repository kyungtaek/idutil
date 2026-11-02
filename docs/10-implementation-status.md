# idutil 0.1.0 구현 및 검증 결과

2026-11-02 최초 구현 기록. 이후 진행한 실제 로컬 설치와 GC 삭제 검증은 [설치 E2E 결과](12-installed-e2e-results.md)에 기록했다. 아래 설치 여부와 GC 미실행 표시는 최초 실행 당시 상태다.

실험용 CLI 구현을 완료했다. 전체 제품 검증 계획을 모두 통과한 상태는 아니다. 사용자 전역 Worktrunk/에이전트 설정에는 설치하지 않았다.

## 구현 범위

- Python CLI: setup, prepare, status, doctor, gc와 내부 Worktrunk hook.
- 저장소·워크트리·프로필·세대별 DerivedData/SourcePackages/Artifacts, 원자적 registry 저장과 프로세스 간 잠금.
- 등록되지 않은 저장소의 hook은 no-op. Worktrunk pre-start가 준비를 완료한 뒤 다음 hook으로 진행한다. pre-remove는 보존 경로를 확인하고 post-remove는 실제 제거 후 참조만 해제한다.
- Codex/Claude 프로젝트 skill과 AGENTS.md/CLAUDE.md 관리 블록 설치, 소유 manifest/hash, 사용자 편집 보존, dry-run/update/uninstall.
- source worktree 외부에 실행 코드 snapshot을 저장하여 해당 worktree 삭제 후에도 hook이 실행된다. 사용한 Python interpreter 자체는 계속 존재해야 한다.
- binding/폴더 손상 또는 선언된 입력 내용 변경 시 새 관리 세대. expected resource ID로 중복 복구를 방지한다. 이전 세대는 보존한다.
- 공유 cache 우회 후에는 다음 세대에도 private 모드를 유지한다. 명시적인 setup --shared-cache로 다시 활성화한다.
- 보존 기간, 소유권/경로/참조/변경 여부를 확인하는 GC 계획과 명시 적용. 보호 산출물과 불확실한 데이터는 유지한다.

## 캐시 정합성의 실제 경계

DerivedData·build DB·intermediates·SPM checkout·Pods 디렉터리를 작업 간 공유하지 않는다. 빌드 그래프와 native cache 적중 판단은 기존 Xcode에 맡긴다. 자체 바이너리 재사용 판정을 구현한 것은 아니다.

| 의존성 | 현재 전략 |
| --- | --- |
| SCM SPM | Package.resolved 등 관찰, 작업별 SourcePackages. 공식 다운로드 캐시는 도구 소유로 유지 |
| local SPM | --input으로 소스·리소스 경로 등록, 내용 hash 변화 시 세대 교체. 실제 컴파일 정합성은 Xcode |
| CocoaPods | Podfile/Podfile.lock 관찰, Pods 설치는 기존 pod 도구. development Pod 입력은 --input으로 등록 |
| static framework/XCFramework | --input으로 vendored 경로 등록, 바이너리 내용 변화 관찰. 자체 framework 공유 저장소 없음 |

각 방식은 독립적으로 사용할 수 있다. 이번 실제 빌드 시험은 네 방식을 조합한 한 fixture이며 모든 단독 조합과 변경 시나리오를 검증한 것은 아니다. 모노레포의 외부 입력, 사용자 빌드 스크립트, 환경 변수 전체를 자동 추적하지 않는다. 관측 범위가 전체 의존성 그래프라고 표시하지 않는다.

공유 compilation cache는 opt-in이며 Xcode 27.0만 허용한다. 저장소·Xcode/SDK·host architecture로 저장소 namespace를 분리하고, 세부 compiler key 정합성은 native cache에 맡긴다. 이 실험은 캐시 연결과 결과 정합성을 확인했으며, 속도 개선율이나 전체 적중률을 입증하지 않는다. native CAS 내부 항목 삭제 및 용량 상한은 구현하지 않았다.

## 검증 결과

환경: macOS arm64, Python 3.9.6, Worktrunk 0.81.0, Xcode 27.0 (27A266a), XcodeBuildMCP 2.7.0. Xcode는 명령별 DEVELOPER_DIR로 지정했고 시스템 xcode-select는 변경하지 않았다.

| 시험 | 결과와 한계 |
| --- | --- |
| 자동화 테스트 | **24개 통과**, 그중 실제 Worktrunk 통합 1개. 생성·전환·삭제, 경로 인용, 복구·동시 요청, GC 보호·변경 감지, 설치 보존/해제 등 |
| 패키지 배포 형태 | uv build로 wheel/sdist 생성 성공; skill template validator 통과 |
| Worktrunk A/B 생성 | 실제 hook으로 서로 다른 DerivedData 준비, 재전환 시 유효 binding 재사용 |
| 혼합 의존성 xcodebuild | macOS A/B 빌드 및 probe 값 정상. 실제 build settings 출력 경로도 관리 경로 내부 |
| xcodebuild test | macOS XCTest 1개 통과, 관리 경로의 테스트 결과 확인 |
| iOS Simulator 대상 | generic arm64 compile/link 통과. 시뮬레이터 부팅·앱 실행·iOS XCTest는 미실행 |
| XcodeBuildMCP 빌드 | 실제 stdio 서버의 defaults를 A→B→A로 바꾸고 build_macos 및 probe 성공. 실제 Claude/Codex 호스트 호출은 아님 |
| XcodeBuildMCP 테스트 | 테스트 자체 1개 통과, **DerivedData 격리 검증 실패**. 아래 상세 참고 |
| 복구 | B의 새 관리 DerivedData/private cache로 실제 재빌드·probe 성공 |
| 다른 워크트리 제거 | 실제 wt로 A를 제거한 뒤 B 재빌드·probe 성공 |
| GC 삭제 | 격리된 임시 데이터에 대한 자동화 테스트 통과. 실제 fixture의 대용량 데이터 GC 적용은 미실행: 사용자의 Xcode가 실행 중이며 강제 종료하지 않음 |
| 실제 Claude/Codex 호스트 4조합 | 미실행. 규칙 파일 생성만으로 에이전트가 실제 적용했다고 판정하지 않음 |
| Xcode GUI | 미검증 |

simctl의 available devices 조회가 12초 후 timeout되어 기기를 만들거나 부팅하지 않았다. 테스트가 아닌 사용자 데이터나 기존 전역 캐시는 삭제하지 않았다.

## XcodeBuildMCP 2.7.0의 제한

`test_macos`는 두 xcodebuild 호출을 사용했다.

1. build-for-testing: 명시한 `-derivedDataPath <A의 관리 경로>` 포함.
2. test-without-building: `-testProductsPath`와 `-resultBundlePath`는 포함하지만 **-derivedDataPath 누락**.

기본 DerivedData의 `MixedCache-gioanahbxyccwcemgjoxhujoxzya`에 TestResults/metadata.db와 Logs가 생성됐다. 기본 경로에 빌드 산출물이 섞였다는 증거는 없다. 사전 파일시스템 스냅샷이 없는 실행이므로 후속 로그·소스·파일 생성 시각 대조라는 증거 한계도 기록했다. 다음 실행용 검증 스크립트에는 전후 snapshot 비교를 추가했다.

MCP 소스의 prepared test 처리에서 derivedDataPath 인수를 제거하므로 단순 extraArgs 추가를 해결책으로 제시하지 않는다. 현재 규칙과 build context는 2.7.0 테스트에 기존 `xcodebuild test`를 사용하도록 안내한다. 다른 MCP 버전/동작은 별도 검증한다.

hook은 서버 세션을 직접 조작하지 않는다. 에이전트가 prepare 결과를 읽고 defaults를 설정·확인한다. 따라서 idutil은 임의 호출이나 MCP 내부 구현을 강제로 막는 격리 장벽이 아니다. 외부 빌드 wrapper를 만들지 않는 현재 제품 범위에서 이 한계를 명시한다.

## 남은 기능과 검증

주기적/무인 자동 GC, native CAS 용량 관리, 자체 seed 저장소, 메뉴바 UI, 시뮬레이터 관리, 에이전트 호스트 자동 감지 및 실제 세션 검증은 구현하지 않았다. 제거 hook은 참조 해제까지만 수행한다. GC 적용은 명시적 사용 종료 확인이 필요하다.

재생성 가능한 데이터라도 `.dSYM`/`.xcresult` 등이 포함되면 현재는 세대 전체를 보존한다. 사용자 검토 없이 중요한 결과를 지우지 않기 위한 보수적 첫 구현이며, 결과 보존과 중간 산출물만의 부분 GC는 후속 과제다.

이번 fixture는 `.lab/mixed-20261102-133033`에 약 1.3 GiB 남아 있다. 사용자 Xcode 실행 상태 때문에 이를 정리 완료로 보고하지 않는다. 도구 다운로드는 `.tools` 아래에만 있다. MCP가 생성한 fixture 전용 외부 로그/결과도 보존했다. 필요한 증거는 아래 별도 디렉터리에 복사했다.

- [자동화 테스트 로그](evidence/implementation-2026-11-02/unit-tests.log)
- [실제 빌드·MCP 실행 보고서](evidence/implementation-2026-11-02/implementation-report.json)
- [검증한 소스 SHA256](evidence/implementation-2026-11-02/source-sha256.json)
- [재현 스크립트](../scripts/verify_xcode.py)

증거 보고서의 절대 경로는 해당 실행 당시 경로다. 이후 Worktrunk로 제거한 A의 경로가 지금 존재한다는 뜻은 아니다. 소스 SHA는 최종 단위 테스트 대상이며 실제 Xcode 실행 후 추가된 진단 문구와 shared-cache 우회 유지 보완도 포함한다.

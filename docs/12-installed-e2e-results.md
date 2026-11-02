# 로컬 설치 및 E2E 결과

2026-11-02. Homebrew/uv로 설치한 실제 CLI와 사용자 설정을 사용했다. **워크트리 생성부터 실제 GC 삭제까지 통과했다.** 설치 방법은 [설치 안내](11-installation.md)에 정리했다.

## 설치된 항목

| 항목 | 설치 결과 |
| --- | --- |
| Worktrunk | 0.81.0, `/opt/homebrew/bin/wt` |
| idutil | 0.1.0, `~/.local/bin/idutil`, uv tool의 별도 환경 |
| PATH | uv가 `~/.zshenv`에 `~/.local/bin` 추가 |
| zsh 연동 | 공식 `wt config shell install zsh --yes`로 `~/.zshrc`에 추가 |
| Worktrunk hook | `~/.config/worktrunk/config.toml`에 세 hook |
| 관리 루트 | `~/Library/Application Support/idutil` |
| 등록 저장소 | `.lab/mixed-20261102-135547/seed` — 이번 E2E 전용 샘플 |

프로젝트 등록은 샘플에만 적용했다. 다른 iOS 앱은 그 저장소에서 별도 setup을 실행해야 한다. 앱의 Codex/Claude 규칙 파일을 설치했으며, 실제 두 호스트의 자율 빌드 세션까지 실행한 것은 아니다.

새 interactive zsh에서 idutil/wt 버전을 확인했고, `wt switch b`가 실제 현재 디렉터리를 B로 바꾸는 것도 확인했다.

## 실행과 결과

테스트는 idutil 내부 함수를 직접 호출하지 않고 PATH의 설치된 CLI를 subprocess로 실행한다. 전용 Worktrunk 설정 파일이나 관리 루트 override를 사용하지 않았다. 샘플 생성만 기존 fixture helper를 사용했다.

| 단계 | 결과 |
| --- | --- |
| setup dry-run → 실제 설치 | 통과, 두 프로필 등록 및 세 hook 연결 |
| 실제 wt로 A/B 생성 | 통과, 생성 hook이 관리 경로 준비, 각 작업의 Codex/Claude 규칙 확인 |
| 재개·전환 | 통과, A 재진입 시 동일 binding 사용 |
| 경로 인용 | 공백과 작은따옴표가 있는 A 경로에서 통과 |
| 혼합 의존성 빌드 | SCM SPM + local SPM/리소스 + development CocoaPod + static XCFramework, A/B macOS 빌드/실행 값 일치 |
| 실제 출력 경로 | 각 xcodebuild settings의 네 출력 경로가 해당 관리 DerivedData 내부임을 확인 |
| macOS 테스트 | 기존 xcodebuild test, XCTest 1개 통과 |
| iOS 빌드 | generic iOS Simulator arm64 compile/link 통과; 시뮬레이터 실행은 제외 |
| 손상 복구 | A의 DerivedData를 다른 이름으로 보존해 누락을 유도, 새 관리 세대 생성 후 빌드·실행 성공 |
| A 제거 | 실제 wt remove 및 post-remove 참조 해제 확인 |
| B 유지 | A 제거 후 기존 B 경로에서 재빌드·실행 성공 |
| 실제 GC | 사용자가 Xcode를 종료한 뒤, 테스트 저장소 소유 후보 4개 삭제. 활성 B 자원 보존 |
| 초기 의존성 해석 후 안정화 | 새 Package.resolved를 반영한 B 세대에서 빌드 → prepare가 동일 ID 재사용, 최종 doctor 정상 |
| 회귀 테스트 | 설치된 Worktrunk로 실제 통합 테스트를 포함해 24개 통과 |

GC가 삭제한 파일의 논리 크기는 **409,916,511 bytes (약 391 MiB)**다. 실행 전후 시스템 여유 공간 차이는 429,993,984 bytes였으며, 다른 프로세스와 APFS의 영향이 있으므로 모두 idutil의 회수량이라고 단정하지 않는다.

첫 빌드가 Package.resolved를 생성해 doctor에서 입력 변경을 보고했다. 이를 무시하지 않고 B를 prepare로 갱신한 뒤 재빌드·재사용·doctor 정상까지 추가 검증했다. 재현 스크립트에도 최초 해석 후 재사용 확인을 추가했다. A는 이미 제거되어 이 추가 단계는 해당 실행에서 B만 직접 확인했다.

E2E 후 status가 정상 삭제된 자원을 '경로 누락' 진단으로 표시하는 문제를 발견했다. 삭제 완료 상태는 0 bytes로 표시하고 오류로 취급하지 않도록 수정하고 회귀 테스트에 포함했다.

## 보존된 데이터와 범위

B 워크트리, 활성 빌드 데이터, 이전 세대의 XCTest 결과는 확인용으로 남아 있다. 최종 관리 데이터 논리 크기는 약 **594 MiB**다. XCTest 결과를 포함한 세대는 보호 규칙 때문에 GC하지 않는다. 샘플의 보존 기간은 E2E를 위해 0시간으로 지정했으며 일반 등록 기본값은 24시간이다.

이번 실행은 기본 private cache 모드다. native 공유 캐시 적중률/성능을 새로 검증한 실행이 아니다. 이전 [구현 검증](10-implementation-status.md)의 MCP 제한도 그대로 적용된다. 사용자 데이터나 이전 실험의 전역 캐시를 삭제하지 않았다.

## 직접 확인하기

```sh
idutil status --measure
idutil doctor --integration \
  --worktree /Users/kent/workspace/ios-disk-util/.lab/mixed-20261102-135547/feature-b
wt -C /Users/kent/workspace/ios-disk-util/.lab/mixed-20261102-135547/seed list
```

[실행 보고서](evidence/installed-e2e-2026-11-02/installed-e2e.json) · [GC 적용 결과](evidence/installed-e2e-2026-11-02/gc-apply.stdout) · [자동화 테스트 로그](evidence/installed-e2e-2026-11-02/unit-tests.log) · [재현 스크립트](../scripts/e2e_installed.py)

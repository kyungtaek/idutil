# 바이너리 빌드와 릴리즈

사용자는 [설치 안내](11-installation.md)의 GitHub Releases 바이너리를 사용한다. 이 문서는 유지보수자용이다.

## 배포물

- `idutil-v<version>-macos-arm64.tar.gz`
- `idutil-v<version>-macos-x86_64.tar.gz`
- `install.sh`, `SHA256SUMS`
- 아키텍처별 `verification-*.json`

PyInstaller one-file 실행 파일에 Python과 tomlkit, agent skill을 포함한다. setup은 바이너리를 관리 루트에 복사하여 hook의 수명을 다운로드 위치와 분리한다. macOS 15 runner에서 각각 빌드·검증한다. ad-hoc 서명이며 Developer ID 서명과 Apple 공증은 별도 배포 작업이다.

[PyInstaller runtime 규약](https://pyinstaller.org/en/stable/runtime-information.html)과 [GitHub macOS runner 목록](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)을 기준으로 구성했다.

## 로컬 빌드

macOS에서 Python 3.12와 Worktrunk를 준비한다.

```sh
uv venv --python 3.12 .tools/release-venv
uv pip install --python .tools/release-venv/bin/python . -r packaging/requirements-build.txt
IDUTIL_BUILD_PYTHON=.tools/release-venv/bin/python scripts/build-binary.sh
python3 scripts/verify_binary.py --binary dist/binary/idutil
```

해당 Mac 아키텍처만 빌드한다. 교차 컴파일을 가장하지 않는다. `verify_binary.py`는 임시 Git 저장소·별도 hook 설정을 사용하며, setup 후 다운로드한 실행 파일을 제거하고도 hook이 생성·준비·제거를 수행하는지 검사한다. 실제 Xcode 빌드는 별도 E2E에 속한다.

## 원격 릴리즈

1. pyproject.toml과 src/idutil/__init__.py 버전을 함께 올리고 `uv lock`을 갱신한다.
2. packaging/RELEASE_NOTES.md와 설치 안내의 고정 버전을 갱신한다.
3. 단위 테스트·설치 스크립트 테스트·standalone 검증을 수행한다.
4. main에 커밋한 뒤 `v<version>` 태그를 푸시한다.
5. GitHub Actions의 **Release macOS binaries**가 Apple Silicon/Intel에서 각각 빌드와 검증을 실행한다.
6. 양쪽 성공 후에만 체크섬을 생성하고 GitHub Release를 게시한다.
7. 게시된 install.sh로 실제 다운로드·설치·버전·hook을 확인한다.

```sh
git push origin main
git tag -a v0.2.0 -m 'idutil v0.2.0'
git push origin v0.2.0
```

workflow_dispatch로 이미 존재하는 태그를 지정할 수도 있다. 이미 게시된 동일 버전의 자산을 자동 덮어쓰지 않는다. 공개한 바이너리 내용을 바꾸려면 새 버전을 배포한다. 원격 CI는 실제 에이전트 세션, Xcode GUI, 시뮬레이터 실행 검증을 대신하지 않는다.

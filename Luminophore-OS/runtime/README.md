# Luminophore 런타임 도구

**한국어** · [English](README.en.md)

이 디렉터리는 content-addressed DE 릴리스를 위한 개발 도구를 구현합니다.
패키지를 설치하거나, 실행 중인 OS를 업데이트하거나, 부트로더를 변경하거나,
기존 데스크톱을 재시작하지 않습니다. 기존 컴포지터 `v1` 아티팩트는
컴포지터 전용 진단 형식으로 유지됩니다. 전체 DE 아티팩트는
`luminophore-release/v2`를 사용합니다.

이 디렉터리에서 `python3 -B luminophore-runtime --help`를 실행하세요.

## 빌드와 패키징

`build-lock`은 모든 source/sysroot 파일, mode, symlink, 인자 벡터와
`SOURCE_DATE_EPOCH`를 기록합니다. 생성된 JSON은 두 입력 트리 바깥에 저장하세요.
`build`는 Bubblewrap을 호출하기 전후로 lock을 다시 검사합니다. 네트워크는 없고,
읽기 전용 sysroot는 `/`, 읽기 전용 source는 `/src`, 비어 있는 쓰기 가능한 output은
`/build`에 둡니다. 먼저 build root에 `/src`, `/build`, `/proc`, `/dev`, `/tmp`
mount point를 준비하세요. 전체 작업 동안 입력은 immutable 상태로 유지해야 합니다.
lock의 전/후 검사는 잠깐 변경됐다가 원래대로 돌아온 mutation을 감지할 수 없습니다.
읽기 전용 filesystem snapshot이나 image를 사용하는 것이 적절합니다.

```sh
python3 -B luminophore-runtime build-lock --sysroot /build-input/root \
  --source /build-input/source --epoch 1700000000 -- /bin/sh /src/build.sh > build-lock.json
python3 -B luminophore-runtime build --sysroot /build-input/root \
  --source /build-input/source --lock build-lock.json --output /build-output
```

전달한 build command는 해당 입력만으로 **모든** 컴포넌트와 전용 의존성을 빌드해
staged sysroot를 만들어야 합니다. 이 도구는 toolchain을 다운로드하거나 개발자
`/usr`에서 package version을 추론하지 않습니다. 재현 가능성을 주장하려면
독립적으로 다시 빌드하고 output digest를 비교하세요.

`pack --sysroot ROOT --recipe RECIPE --output ARTIFACTS`는 staged root를 입력으로
받습니다. recipe에는 다음 필드가 모두 필요합니다.

| 필드 | 의미 |
|---|---|
| `schema` | 정수 `1` |
| `files` | artifact 상대 destination → sysroot 상대 file/tree map |
| `providers` | SONAME → `{ "path": "usr/lib/…", "owner": "private" or "system" }` map |
| `dynamic` | dlopen/ctypes/GI로 불러오는 명시적 sysroot 상대 ELF path |
| `entries` | 이름이 있는 컴포넌트. 각 항목은 `{ "path": "bin/…", "args": [] }` |
| `compatibility` | 양의 정수 `config`, `ipc` version |
| `provenance` | 비어 있지 않은 `source`, SHA-256 `build_lock`, 비어 있지 않은 `license` |
| `system_files` | command/service/driver/resource용 추가 OS 상대 file → SHA-256 pin |
| `runtime_env` | 필요에 따른 `PYTHONHOME`, `GI_TYPELIB_PATH`, `GIO_MODULE_DIR`, `GSETTINGS_SCHEMA_DIR` 상대 directory |

`compositor`, `control`, `shell` entry는 필수입니다. entry는 ELF file을 실행하므로
script를 사용할 경우 번들된 interpreter를 path로 지정하고 script는 `args`에 넣어야
합니다. Shell consumer가 실행 가능한 control-client path를 기대하므로 `control`에는
prefix argument가 없어야 합니다. 인자의 `{release}`는 immutable release directory로
확장됩니다. compositor entry에는 패키지된 configuration/autostart file을 불러오는
인자가 포함되어야 하며 working directory에서 추론하지 않습니다. Python shell
entry는 다음과 같은 형태입니다.

```json
{"path":"python/bin/python3","args":["-B","-s","{release}/shell/luminophore-shell","daemon"]}
```

통합 release autostart dispatcher는 현재 `shell.path`가 번들된 Python interpreter라고
가정합니다. standard library, site-packages(profile worker 의존성 포함),
GTK/GI/typelib, layer-shell, native helper, schema, asset, license, configuration을
명시적으로 mapping해야 합니다. host를 가리키는 Python virtualenv symlink는
전용 runtime으로 간주하지 않습니다. `support/` directory는 예약되어 있으며,
packer는 restart 시 동일한 버전의 launch code를 사용하도록 dispatcher를 여기에
배치합니다.

packer는 명시된 provider를 기준으로 ELF `NEEDED` entry를 재귀적으로 resolve하고,
SONAME을 검사하고, symbol-version name과 interpreter를 기록한 뒤 private provider를
`lib/`로 복사합니다. provider가 없으면 fail-closed로 종료합니다. system provider와
그 transitive dependency는 system-owned 상태를 유지해야 하며 system→private edge는
거부됩니다. 이렇게 해서 한 process에 공유 graphics dependency 두 버전이 섞이는
사고를 방지합니다. glibc/ELF loader와 지원 GPU stack은 OS profile에 두고, 서로
일치하는 GPU userspace/kernel 조합 전체를 유지하세요.

복사되는 각 ELF에는 release의 `lib/`를 가리키는 relocation-safe RUNPATH가 이미
있어야 합니다. 예를 들어 `bin/Hyprland`는 `$ORIGIN/../lib`를, `lib/` 안의 library는
`$ORIGIN`을 사용합니다. `pack --patchelf /absolute/path/to/patchelf`로 staging copy의
RUNPATH를 다시 쓸 수 있습니다. 해당 도구는 build environment에서 pin해야 합니다.
global `LD_LIBRARY_PATH`, `ldconfig` 변경, SONAME compatibility symlink는 만들지
않습니다. source symlink는 제공된 root 안에서 resolve되어야 하며 artifact file은
일반 copy입니다. 예상하지 않은 file/mode/hash와 path escape는 검증 실패로 처리됩니다.

`provenance.build_lock`은 reference일 뿐 **해당 binary가 실제로 그 lock 아래에서
빌드됐다는 attestation이 아닙니다**. hash integrity 역시 signature나 license audit가
아닙니다. 공개 전에 release process에서 build receipt, provenance, package별
license/SBOM, signature, 독립 rebuild evidence를 연결해야 합니다. packer는 항상
session acceptance를 `NOT_RUN`으로 기록합니다.

기존 compositor script는 legacy host CMake build를 실행하지 않고 다음 명시적 mode를
dispatch합니다.

```text
build-canary --locked-build SYSROOT SOURCE LOCK OUTPUT
build-canary --pack-release STAGED_SYSROOT RECIPE ARTIFACTS
```

## 데스크톱 패키징 프로필

### 고정된 컴포넌트 빌드와 assembly

`build-desktop`은 locked builder를 desktop packer와 연결합니다. lock에 기록된 command를
실행하고 생성된 전체 output tree를 build receipt와 대조해 검증한 뒤, 선언된 package
root를 하나의 staged sysroot로 assembly합니다. 실행 중인 머신에서 toolchain을
구성하거나 package를 다운로드하지 않습니다.

제공된 component builder를 위해 다음 구조의 immutable source tree를 준비하세요.
`runtime/`은 이 도구들의 복사본입니다.

```text
source/
  compositor/                    # 고정된 전체 소스 + vendored dependency
  config/                        # 일치하는 shell 소스와 native helper
  release-config/                # 검토된 release-aware Lua configuration
    hyprland.lua
    autostart.lua
  packages/                      # 준비되고 고정된 dependency package root
    <package-name>/usr/...       # private Python/GI/runtime 및 OS profile file
  runtime/scripts/build-components.py
  glaze/                         # 선택 사항: 고정된 FetchContent source override
  layout.json
```

builder는 빌드 전에 compositor와 shell source를 `/build/work`로 복사합니다.
compositor CMake가 source directory에 generated file을 쓰기 때문에 필요한 절차입니다.
compositor/control/watchdog와 shell glow/background/drag helper를 컴파일한 뒤 product
root를 `/build/packages/luminophore-compositor`와
`/build/packages/luminophore-shell`에 기록합니다. `/src/packages`도 함께 복사합니다.
build 전용 helper script만 사용하며 deployment script나 service를 시작하는 installer는
호출하지 않습니다. Bubblewrap의 network access는 계속 비활성화됩니다.

toolchain root에는 호환되는 CMake/Ninja/compiler, Python, protocol generator와 개발
의존성이 미리 들어 있어야 합니다. 준비된 dependency package root는 완전한 private
interpreter/stdlib/module, GI/resource와 system profile을 제공해야 합니다. 이들은
고정된 입력에서 생성되고 layout에 개별 항목으로 기록되어야 합니다. 이 script는
Python을 빌드하거나 distribution repository를 resolve하지 않으며, 서로 다른 compositor
SDK를 대상으로 optional window-glow plugin을 build/install하지도 않습니다. plugin을
활성화한다면 정확히 일치하는 built package를 명시적으로 제공하세요. release config나
public greeter/session unit도 현재 개인 설정에서 추론하지 않습니다.

`source/glaze/CMakeLists.txt`가 있으면 component builder는 해당 고정 tree를
`FETCHCONTENT_SOURCE_DIR_GLAZE`로 전달합니다. 이렇게 하면 미리 채워둔
`_deps/glaze-src` build directory 없이도 깨끗한 offline CMake configuration이
가능합니다. override는 locked source tree 내부에 있어야 합니다. 그렇지 않다면
toolchain이 호환되는 설치형 Glaze CMake package를 제공해야 합니다. download를
비활성화한다고 dependency가 자동 제공되는 것은 아닙니다.

`profiles/cachyos-2026-09-20.lock.json`은 구체적으로 선택된 **build-toolchain input**입니다.
정확한 cached package archive name/version, archive 및 signature SHA-256 hash,
verification-key hash, source commit, extraction normalization을 기록합니다. 일부 archive는
x86_64_v3를 대상으로 하므로 범용 CPU profile이 아닙니다. package list는 private-runtime
ownership policy나 desktop release가 아닙니다. 배포되는 DE artifact에 들어가면 안 되는
compiler와 개발 의존성도 포함합니다.

이 입력을 재구성할 때는 extraction 전에 모든 archive/signature와 verification key를
고정된 hash와 대조하세요. distribution key file이 ASCII armored라면 임시 binary keyring을
사용하고 detached signature를 검증한 뒤, package install script를 실행하지 않고 새로운
비특권 root에 extract합니다. `filesystem`을 먼저 extract하고 `.PKGINFO`, `.BUILDINFO`,
`.MTREE`, `.INSTALL`은 제외하며 containment check를 유지하고, 명시된 normalization
path에는 owner-read permission을 추가합니다. `/src`, `/build`, `/proc`, `/dev`, `/tmp`
mount point를 준비하세요. `build-lock`은 package version string만이 아니라 실제 결과
file과 mode를 bind합니다. 이 build root를 준비하기 위해 실행 중인 host에 package-manager
transaction을 수행하면 안 됩니다.

`profiles/cachyos-python-2026-09-20.lock.json`은 정확한 base에 Pycairo, Python D-Bus,
NumPy와 해당 BLAS/LAPACK dependency archive, 프로젝트가 pin한 OpenCV headless wheel을
추가합니다. wheel은 content-pinned이지만 distribution package signature는 없습니다.
입력을 합치기 전에 base profile hash를 검증하세요. GUI는 Cairo와 D-Bus를 직접 import하고
offline profile worker는 NumPy/OpenCV를 import하므로 Pillow/requests/PyGObject만으로
완전한 shell runtime을 구성할 수 없습니다. 별도 root에서 import probe를 통과했다고
relocated private release가 작동하는 것이 증명되지는 않습니다. RUNPATH,
typelib/resource path, host/private ownership은 여전히 artifact check와 runtime
acceptance가 필요합니다.

`layout.json`은 locked source의 일부입니다. 정확한 필드는 다음과 같습니다.

| 필드 | 계약 |
|---|---|
| `schema` | `"luminophore-build-layout/v1"` |
| `desktop` | 아래의 Desktop input에서 `packages`를 제외한 값. `provenance`는 `source`와 `license`만 포함하고 `system_files`는 path list |
| `packages` | `root`, `name`, `version`, `owner`, `license`, `license_files` record |

각 package `root`는 build output 기준 상대 경로입니다. 예:
`packages/luminophore-shell`. 내용은 이미
`usr/lib/luminophore/shell/luminophore-shell` 같은 최종 sysroot path를 가져야 합니다.
그 밖의 package field는 아래 desktop inventory와 같은 의미입니다. `license_files`는
해당 package root 기준 상대 path입니다. product license path는
`usr/lib/luminophore/share/licenses/luminophore-shell/LICENSE`와
`usr/lib/luminophore/share/licenses/luminophore-compositor/LICENSE`입니다.
실제 source/package version을 사용하고 알 수 없는 version을 verified로 표시하지 마세요.

assembly는 복사된 byte에서 `files` hash와 system-file pin을 계산하고 실제 build-lock
digest를 provenance에 주입합니다. byte가 같더라도 file collision, 겹치는 package root,
root 밖으로 나가는 symlink, 추적되지 않은 source/output mutation, 누락된 desktop
dependency가 있으면 publication 전에 실패합니다. 내부 file symlink는 일반 copy로
변환합니다. directory symlink와 special file은 거부됩니다. 작업 내내 input은
immutable이어야 합니다. 전/후 hash로는 중간에 바뀌었다가 복원된 변경을 감지할 수
없습니다.

```sh
python3 -B luminophore-runtime build-lock --sysroot /inputs/toolchain \
  --source /inputs/source --epoch 1700000000 -- \
  /usr/bin/python3 -B /src/runtime/scripts/build-components.py > build-lock.json
python3 -B luminophore-runtime build-desktop --sysroot /inputs/toolchain \
  --source /inputs/source --lock build-lock.json --layout layout.json \
  --build-output /outputs/build --output /outputs/assembly
python3 -B luminophore-runtime verify-assembly --assembly /outputs/assembly
python3 -B luminophore-runtime pack-assembly --assembly /outputs/assembly \
  --output /outputs/releases --patchelf /build-tools/patchelf
```

build log는 stderr로, CLI result JSON은 stdout으로 출력됩니다. 기존 output assembly는
덮어쓰지 않습니다. 실패한 build는 진단용 build directory를 남기지만 assembly를
publish하지 않습니다. assembly publication은 sibling staging directory,
file/directory fsync, rename을 사용합니다. 공개된 directory에는 `root/`,
`desktop-input.json`, `build-receipt.json`, `build-layout.json`과 content digest가
반영된 `assembly.json`이 들어 있습니다.

이미 `build`로 빌드한 결과는
`assemble-desktop --source … --build-output … --lock … --receipt … --layout layout.json --output …`
을 사용하세요. receipt와 source/output identity가 일치해야 합니다. caller가 제공한
receipt는 command가 실제 실행됐다는 attestation이 아닙니다. `build-desktop`은 현재
process에서 실행을 수행합니다. signing과 독립 rebuild evidence는 여전히 별도의
release 책임입니다.

`pack-assembly`는 assembly manifest, receipt, layout을
`metadata/build-assembly.json` 안에 보존합니다. read/copy는 검증된 file hash와 executable
mode에 묶이므로 검증 이후 변경된 byte가 이전 build evidence 아래서 publish되지 않습니다.
자기 일관성이 있는 digest만으로 authentication이 되지는 않습니다. 신뢰할 수 있는
release process를 통해 input과 기대 identity를 확보하세요. fixture test는 CLI
build → assembly → packaging 전체에서 실제 isolated ELF builder를 실행하지만,
실제 compositor를 compile하거나 production Python/GI/graphics stack 전체를 검증하지
않습니다.

### 데스크톱 layout과 inventory

`desktop-recipe`와 `pack-desktop`은 generic packer 위에 구체적인 desktop-session
layout을 제공합니다. 실행 중인 `/usr`가 아니라 **미리 준비된 build output**을 사용하며
package를 설치하지 않습니다. staged sysroot 안에 다음 tree를 준비하세요.

```text
usr/lib/luminophore/
  bin/Hyprland
  bin/hyprctl
  python/bin/python3
  python/lib/python3.X/                 # 일치하는 전체 stdlib + extension
    encodings/__init__.py
    site-packages/                      # 완전한 dependency set. 예:
      gi/__init__.py                    # 대응하는 _gi*.so 포함
      PIL/__init__.py
      requests/__init__.py
      cairo/__init__.py                 # _cairo*.so 포함
      dbus/__init__.py                  # top-level _dbus_bindings*.so 포함
      numpy/__init__.py                 # _core/_multiarray_umath*.so 포함
      cv2/__init__.py                   # cv2*.so와 wheel native library 포함
  shell/luminophore-shell
  shell/luminophore_shell/              # 전체 shell package
    __main__.py
    bootstrap.py
    assets/
    templates/
  config/hyprland.lua                   # release-aware entry configuration
  config/autostart.lua                  # generation-pinned autostart
  lib/libgtk4-layer-shell.so.0
  lib/girepository-1.0/                 # 전체 typelib closure. 예:
    Gtk-4.0.typelib
    Gtk4LayerShell-1.0.typelib
  lib/gio/modules/                      # 일치하는 native GIO module
  share/glib-2.0/schemas/gschemas.compiled
```

추가 command, plugin, configuration, asset, resource를 포함해 staged subtree 전체를
복사합니다. 외부 private ELF provider는 기존 recursive scanner가 추가합니다.
`metadata`, `support`, `release.json`은 예약된 top-level name입니다. source symlink는
sysroot 내부에 있어야 하고 private Python은 host를 다시 가리키면 안 됩니다.
compositor의 Lua module path와 include를 이 release의 config directory를 불러오도록
설정하세요. 생성된 entry는 `--config {release}/config/hyprland.lua`를 전달하며 Lua를
다시 쓰지 않습니다.

JSON input에는 정확히 다음 필드를 제공하세요.

| 필드 | 값 |
|---|---|
| `schema` | `"luminophore-desktop-input/v1"` |
| `python_version` | build된 interpreter와 일치하는 명시적 major.minor. 예: `"3.14"` |
| `compatibility`, `provenance`, `providers`, `dynamic`, `system_files` | 위 generic recipe와 동일한 계약 |
| `packages` | 아래 형식의 비어 있지 않은 package record array |

각 package는 `name`, `version`, `owner`(`private` 또는 `system`), `license`
(publisher의 license expression), `license_files`(sysroot-relative path),
`files`(sysroot-relative path → SHA-256)를 가집니다. 고정된 package/build input에서
file list와 version을 얻은 뒤 staged sysroot의 해당 path를 hash하세요. 분류되지 않은
모든 library가 private라고 가정해 ownership을 만들면 안 됩니다. 포함된 모든
resource, 복사된 provider, 선언된 dynamic module, 고정된 OS file(loader alias 포함)은
일치하는 ownership class와 digest를 가진 owner 하나에 정확히 속해야 합니다.
하나의 package가 private/system 경계를 가로지를 수 없습니다. 의도한 구성이라면 build
inventory를 명시적인 record로 분리하세요.

모든 private package는 자신의 file list에 속한 license file을 최소 하나 제공해야 합니다.
해당 text는 `licenses/<package>/<index>-<basename>`으로 복사됩니다. 이 record는
publisher declaration이며 license audit, signed provenance, SPDX/CycloneDX SBOM이
아닙니다. license 이름 문자열만으로 그 조건을 검증할 수 없습니다.

```sh
python3 -B luminophore-runtime desktop-recipe --sysroot /build-output/root \
  --input desktop-input.json > desktop-recipe.json
python3 -B luminophore-runtime pack-desktop --sysroot /build-output/root \
  --input desktop-input.json --output /build-output/artifacts \
  --patchelf /build-tools/patchelf
```

`desktop-recipe`는 read-only inspection command입니다. 실제 release에는
`pack-desktop`을 사용하세요. 이 command는 검사를 반복하고 system hash 및 복사된 source
byte를 inventory에 bind하며 generation digest 아래
`metadata/desktop-inventory.json`을 포함합니다. inspection recipe를 generic `pack`에
직접 전달하면 이런 추가 inventory check나 metadata가 **유지되지 않습니다**.
input hash는 원래 build output을 설명하고, artifact manifest는 RUNPATH patch 이후
byte를 설명합니다. packaging 전체에서 sysroot는 immutable 상태로 유지하세요. 검사가
실패해도 새 generation은 publish되지 않으며 기존 generation은 그대로 유지됩니다.

생성되는 entry에는 compositor, control, shell daemon, standalone settings가 포함됩니다.
private Python/GI/resource path는 기존 session dispatcher를 사용합니다. profile은
structure completeness와 ELF closure를 검사할 뿐 Python import 성공, typelib
compatibility, feature completeness, graphical session을 검증하지 않습니다. test는
**fixture-only** Python/resource 내용을 가진 실제 ELF program을 사용합니다.
production toolchain/dependency sysroot, 제공 builder 또는 프로젝트 전용 builder를
사용한 전체 component build, import/resource probe, greeter packaging, public
session/service installation, graphical acceptance는 여전히 release integration 작업입니다.
이 fixture test로 전체 desktop release가 build되거나 accepted된 것은 아닙니다.

## 검증과 선택

`verify --release DIR`은 전체 artifact와 content identity를 검사합니다.
`--host-root /`를 주면 OS file pin도 검사합니다. `--loader`는 pin된 ELF loader의
`--list` mode를 사용해 private library와 선언된 dynamic module을 포함한
**실제 resolve된 library path와 hash**를 manifest와 비교합니다. compositor entrypoint는
호출하지 않습니다. loader preflight는 신뢰할 수 있는 artifact에서만 사용하세요.
static/loader 검사를 통과해도 GPU, input, Python import, portal, resource,
graphical-session acceptance가 증명되지는 않습니다.

`stage --store DIR --release ARTIFACT --trusted-generation SHA256`은 immutable-file
generation을 추가하고 candidate를 기록합니다. trusted digest는 별도로 검증된 channel에서
얻어야 합니다. stage는 selected release를 변경하지 않습니다. `status --store DIR`은
state-changing command에 필요한 revision을 반환합니다.

`select --store DIR --generation SHA256 --revision N --config-version V`는 현재 host와
loader를 검증하고 **다음 세션**의 selection을 변경합니다. `--host-root` override는
fixture/offline check용이며 chroot나 loader override가 아닙니다. live loader preflight는
계속 실제 host를 사용합니다. 오래된 revision, revoked release, incompatible config
version, 없거나 변경된 OS pin은 실패합니다.

selected/previous/confirmed reference는 하나의 fsync된 JSON record에 저장되며 process
lock 아래 atomic replace로 갱신됩니다. rollback은 같은 record에서 selected와 previous를
맞바꿉니다. replace 이전 write가 실패하면 기존 state를 계속 읽을 수 있습니다.
고아 `.staging-*` directory는 candidate나 GC input이 아닙니다.

`confirm`은 selected generation, 정확한 selection revision, 일치하는 profile digest,
현재 boot ID와 `physical` evidence boundary를 요구합니다. `compositor`, `shell`,
`ipc`, `output`, `input`, `lock`, `resume`, `portal` check가 모두 JSON `true`여야 합니다.
현재 host byte와 loader resolution도 다시 검사합니다. evidence는 외부 collector/operator가
제공하며 이 CLI가 physical test result를 만들어내거나 암호학적으로 attest하지 않습니다.

`revoke`는 이미 실행 중인 process와 file은 유지하면서 새로운 selection/session admission을
막습니다. `gc`는 selected/previous/confirmed/candidate generation과 process가 잡고 있는
shared lease를 보존합니다. GC는 삭제 전에 exclusive lease를 획득합니다. child process가
상속받은 lease는 원래 supervisor가 종료된 뒤에도 generation을 유지합니다. 상속 descriptor를
의도적으로 모두 닫고 detach하는 program은 향후 설치형 session/cgroup adapter가
contain해야 합니다. 이것은 일반적인 daemon discovery mechanism이 아닙니다.

## 세션과 애플리케이션 경계

`run --store DIR --component compositor --config-version V`는 selection을 한 번 resolve하고
lease를 유지한 채 해당 entry를 실행합니다. `--generation SHA256`은 다른 component를
restart할 때 사용하는 고정 path입니다. release autostart로 실행된 shell은 해당
generation과 번들 dispatcher/Python을 지정하는 quoted command를 받습니다.
`--restart-limit`은 최대 5회 실패로 제한됩니다. shell supervisor는 소유 session의 PID와
start time을 감시하고 owner가 사라지면 종료합니다. signal handling은 child process group에
전달하며 SIGKILL로 escalation하기 전에 종료 시간을 제한합니다.

외부 application environment에서는 private loader/Python/GI/plugin search path를 제거하고
system PATH를 사용합니다. Shell 시작 시 GI path는 in-process repository로 옮겨지고
Python/GI environment override는 제거됩니다. 내부 capture/profile worker는
`internal_python_environment`로 선언된 private path를 다시 구성합니다. v2 shell은
검증된 release control entry에서 `hyprctl`을 resolve합니다. release autostart는 이름이
지정된 display variable만 D-Bus/systemd로 import하며 legacy fixed-source shell target은
시작하지 않습니다.

`config-copy`는 generation별로 분리된 fsync settings tree를 만들고 overwrite를 거부합니다.
공유 source는 변경하지 않습니다. schema migration을 **임의로 만들지 않습니다**.
caller가 transition을 구현하고 test하거나 같은 config version을 유지해야 합니다.
public session registration, polkit/portal/service unit, desktop-file activation,
session/cgroup deployment에는 설치된 adapter가 필요합니다. development CLI는 그 설치
자체가 아닙니다.

## 관리형 OS admission 경계

`os-describe`, `os-verify`, `os-admit`은 inactive root tree(package DB, module, OS identity
포함), 분리된 boot asset(kernel, initramfs, entry), DE generation과 profile digest를
bind합니다. byte/mode/symlink가 하나라도 바뀌면 다른 deployment가 됩니다.
실행 중인 `/`와 `/boot`는 candidate root로 거부됩니다. VM evidence로 `trial`을 admit할
수 있고, `confirmed`는 같은 deployment에 대한 physical evidence도 추가로 요구합니다.
필수 check에는 session function, rollback, interrupted update가 포함됩니다. admission은
evidence를 `external_report`로, boot activation을 `NOT_RUN`으로 표시합니다.

이것은 content/admission contract입니다. inactive OS를 업데이트하거나, 특정 entry가
지정 root를 실제로 boot한다는 것을 증명하거나, boot entry를 설치하거나, trial boot
counter를 구현하거나, firmware fallback을 선택하거나, 실행 중인 OS를 switch/rollback하지
않습니다. 해당 작업에는 선택한 boot/root layout에 맞는 검증된 host adapter가 필요합니다.
그 adapter가 존재하고 VM 및 physical test를 통과하기 전까지는 이 코드가 설계상의 강한
보장인 “system update가 실행 중인 desktop을 변경할 수 없다”를 제공한다고 볼 수 없습니다.
기존 kernel-only PostTransaction observer는 변경되지 않았습니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```

test는 실제 transitive ELF fixture를 compile/run하고 build root를 제거하며 provider
loss/tampering/profile drift를 주입합니다. selection CAS와 실패한 atomic write를
검증하고 child lifetime 동안 lease를 유지하며, session-owned worker를 정지시키고,
packaging CLI와 Lua autostart를 실행하고, OS admission/config isolation을 확인합니다.
실제 Bubblewrap test에는 user/network namespace가 필요하므로 제한된 outer sandbox에서는
skip될 수 있습니다. 이 fixture는 패키지된 Luminophore desktop이나 physical boot test가
아닙니다.

loader/tool 참고:
[Bubblewrap](https://github.com/containers/bubblewrap/blob/main/bwrap.xml),
[patchelf](https://github.com/NixOS/patchelf/blob/master/patchelf.1).

## 명시적 provider 분류

`classify-providers --sysroot ROOT --policy POLICY.json`은 desktop input이 사용하는
provider map을 반환합니다. policy에는 `candidates`(SONAME → root-relative file),
`system`(OS SONAME seed), `required_private`(반드시 private로 유지할 SONAME)이 있습니다.
seed와 private requirement는 문자열 array입니다. command는 각 OS seed의 ELF dependency와
interpreter를 OS set으로 확장합니다. required-private SONAME에 대한 dependency가 생기면
ownership을 조용히 바꾸지 않고 실패합니다. missing provider와 SONAME mismatch도
실패합니다.

지원 graphics/loader/service package policy에서 OS seed를 선택하고 dynamically loaded
OS driver module은 명시적으로 포함하세요. 이 command는 host library를 발견하거나 GPU
compatibility를 증명하지 않습니다. private ELF entry/module closure와 typelib의
`dlopen` provider도 desktop input에 계속 포함해야 합니다. artifact packing은 file별
RUNPATH를 다시 쓰므로 global library path는 필요하지 않습니다. relocation 이후에는
loader probe와 실제 Python/GI import probe를 실행하세요. 유효한 ELF metadata만으로
Python extension compatibility나 resource discovery를 증명할 수 없습니다.

`profiles/cachyos-providers-2026-09-20.json`은 고정 CachyOS toolchain과 Python 추가분에
대한 candidate와 OS seed를 기록합니다. candidate path는 private-prefix relocation
이전의 merged input root를 가리킵니다. 이것은 input policy이며 hardware support
declaration이나 minimal package list가 아닙니다.

`profiles/patchelf-0.19.1.lock.json`은 relocation tool archive와 executable을 pin합니다.
ELF `--list` 성공은 native initializer를 실행하지 않으므로 실제 import/dlopen도 sandbox에서
검증하세요. 최소 OS test root를 만들 때 OS SONAME alias를 명시적 system file pin으로
보존하세요. 다른 ABI에 대한 alias를 임의로 만들면 안 됩니다. OpenCV는 process-local
`LD_LIBRARY_PATH`를 변경할 수 있으므로 원래 process input뿐 아니라 실행된 child
environment도 확인하세요.

## 독립 CachyOS 패키지

Arch package template은
`../../compositor/luminophore/packaging/arch/PKGBUILD.in`에 있습니다. Hyprland에 대해
`provides`, `conflicts`, `replaces`를 선언하지 않습니다. public command는
`luminophore-compositor`, `luminophore-session`, `luminophorectl`,
`luminophore-shell`입니다. login entry는 `luminophore.desktop`이며 portal identity는
`org.freedesktop.impl.portal.desktop.luminophore`입니다.

이미 검증된 whole-desktop generation에서 실제 package를 준비하려면:

```sh
PYTHONPATH=. python3 -B -m luminophore_runtime.packaging \
  --release /build/releases/GENERATION --version 0.1.0rc1 --output /build/package
cd /build/package
makepkg
```

version은 명시적이며 pacman version ordering에 따라 증가해야 합니다. content hash는
generation identity이지 package version이 아닙니다. packaging 과정에서 release member를
strip/purge하거나 documentation을 recompress하는 등 byte를 다시 쓰지 마세요. 해당 hash는
package extraction 뒤에도 유지되어야 합니다. relocation에는 `patchelf >= 0.19.1`이
필요하며 version과 digest는 artifact에 포함됩니다. 현재 distro ELF input에서는 loader
listing이 통과했는데도 0.17.2에서 dynamic-loader crash가 발생했습니다. 따라서 process
probe는 필수입니다.

package activation은 immutable release를 root-owned
`/var/lib/luminophore/generations`로 복사하고 host와 loader를 검증한 뒤 atomically
select합니다. display manager를 바꾸거나 desktop을 restart하거나 user configuration을
지우지 않습니다. 호환되지 않는 최초 activation은 관리 가능한 빈 store를 남기고,
호환되지 않는 upgrade는 이전 selection을 보존합니다. session child는 read-only flock
lease를 유지합니다. 활성 lease가 있으면 package removal을 거부하며 승인된 removal 중에는
새 session을 막습니다. 보존된 generation과 user setting은 package removal 뒤에도
남아 있습니다.

작은 management launcher는 host Python standard library를 사용합니다. shell, 해당 Python
module, GTK/GIO resource, Qt picker, compositor dependency는 release payload를 사용합니다.
PolicyKit authentication agent, `grim`, generic portal, PipeWire, logind, kernel, GPU stack은
선언된 system boundary로 남습니다. private portal build에는 hash-locked
`portal-upstream` directory와 fallback font 및 Qt Wayland plugin을 포함한
`release-resources`가 필요합니다. product Lua default는 `defaults/hyprland.lua`와
고정된 config source에서 가져옵니다. 개인 output name이나 developer checkout path를
startup default로 사용하지 않습니다.

## 업데이트 계약과 acceptance 경계

Luminophore는 global ALPM update-blocking hook을 설치하지 않습니다. 관련 없는 systemd
service를 포함한 CachyOS package transaction은 pacman의 일반 ownership/conflict rule에
따라 진행됩니다. compositor와 shell은 package name, executable path, configuration root,
systemd unit, session entry, runtime directory, private release dependency를 통해 system
Hyprland session과 분리됩니다.

kernel, GPU, login, portal과 그 밖의 shared-system 변경은 여전히 desktop session에 영향을
줄 수 있습니다. update 전에 요청하면 제안된 CachyOS transaction에 대해 compatibility를
검토하고 update 뒤 다시 검증합니다. 이 검토는 advisory이며 명시적으로 요청되는 절차입니다.
관련 없는 package transaction을 자동 차단하지 않습니다. DE rollback은 kernel이나 GPU를
rollback하지 않습니다.

user setting은 독립 Luminophore config directory 아래에 저장됩니다. 기존 file은
보존합니다. schema transition에는 명시적인 migration이 필요하며, 들어오는 manifest의
version을 user config version의 증거로 삼지 않고 거부합니다. generic portal process는
desktop/backend selection을 cache하므로 Luminophore display environment를 import한 뒤
refresh합니다. 같은 user의 동시 graphical session은 이 lifecycle에서 지원하지 않습니다.

유용한 검증 entrypoint:

- 이 runtime directory에서 `python3 -B -m unittest discover -s tests`.
- 일치하는 isolated host root **안에서** `scripts/probe-release.py RELEASE`:
  ELF loading, private GTK/GIO TLS, portal version, Lua config, offscreen Qt
  initialization을 확인합니다. session 및 physical acceptance는 `NOT_RUN`으로 보고합니다.
- `luminophore_test=1`로 boot한 disposable QEMU root에서
  `tests/vm-pacman-coexist.sh`: 두 installation order, package version upgrade,
  독립 제거, immutable Hyprland file, 변경된 shared-library fixture의 pre-unpack rejection을
  검사합니다. 이 script는 의도적으로 빈 test pacman database와 `--nodeps`를 두 번
  사용합니다. transaction/file ownership과 hook을 검사할 뿐 깨끗한 distribution의
  dependency completeness나 graphical login을 검증하지 않습니다.
- `tests/vm-desktop-package.sh`는 실제 whole-DE package로 두 installation order와
  shared library rejection을 반복하고 설치된 private runtime probe를 실행합니다.
  `tests/vm-check-desktop.py`를 `/fixtures/check-desktop.py`로,
  `scripts/probe-release.py`를 `/fixtures/probe-release.py`로 복사하고 disposable image에
  일치하는 signed-package system input을 준비하세요. boot 전에 해당 root 안에서
  `ldconfig`를 실행하고 profile을 다시 확인해야 합니다. 이전 package를 제거하지 않은 채
  오래된 package를 overlay하면 obsolete versioned library file 때문에 `ldconfig`가 잘못된
  version을 선택할 수 있습니다. 이 script도 `--nodeps`를 두 번 사용하며 graphical
  session이나 repository dependency closure를 증명하지 않습니다.

local build receipt는 독립적인 reproducibility attestation이 아닙니다. 새 desktop release를
배포하기 전에 DM login, screen sharing, input, monitor hotplug, lock/resume, physical GPU
acceptance를 별도로 수집해야 합니다.

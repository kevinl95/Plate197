#!/usr/bin/env bash
# Build the Plate197 image.
#
#   distro/build.sh                          the defaults
#   PLATE197_LATITUDE=42.3 distro/build.sh   somewhere else
#   PLATE197_KIOSK=no distro/build.sh        service only, no display stack
#
# Needs sudo (it loop-mounts an image) and about 6GB of disk. The first
# run clones CustomPiOS and downloads the base image; later runs reuse
# both. The finished image lands in distro/src/workspace/.
#
# On an x86_64 host this also needs qemu-aarch64-static registered with
# binfmt, or the chroot cannot run a single arm64 binary; distro/README.md
# lists the packages. Everything configurable is an environment variable,
# so this stays non-interactive.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "${HERE}/.." && pwd)"
CUSTOMPIOS="${CUSTOMPIOS:-${HERE}/CustomPiOS}"
CUSTOMPIOS_REF="${CUSTOMPIOS_REF:-master}"
IMAGE_DIR="${HERE}/src/image-raspberrypiarm64"

# Pinned, and pinned to Bookworm on purpose. Raspberry Pi OS Lite arm64
# moved to Trixie (Python 3.13) in late 2025, and there is no arm64
# tflite-runtime wheel for 3.13 — that image works, but it has to carry
# ai-edge-litert at 14MB instead of tflite-runtime at 2.3MB. Bookworm is
# Python 3.11, where the small interpreter still has wheels.
BASE_IMAGE_URL="${BASE_IMAGE_URL:-https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2024-11-19/2024-11-19-raspios-bookworm-arm64-lite.img.xz}"

say() { printf '\n== %s\n' "$*"; }

say "CustomPiOS"
if [ ! -d "${CUSTOMPIOS}" ]; then
    git clone --depth 1 --branch "${CUSTOMPIOS_REF}" \
        https://github.com/guysoft/CustomPiOS.git "${CUSTOMPIOS}"
fi

say "wiring this distro to it"
# build_dist is CustomPiOS's own launcher, verbatim from its dist template;
# custompios_path is how it finds the framework.
if [ ! -f "${HERE}/src/build_dist" ]; then
    cp "${CUSTOMPIOS}/src/dist_generators/dist_example/src/build_dist" "${HERE}/src/build_dist"
fi
chmod +x "${HERE}/src/build_dist"
"${CUSTOMPIOS}/src/update-custompios-paths" "${HERE}/src"

say "base image"
mkdir -p "${IMAGE_DIR}"
if ! ls "${IMAGE_DIR}"/*.img.xz >/dev/null 2>&1; then
    curl -L --fail --retry 3 -o "${IMAGE_DIR}/$(basename "${BASE_IMAGE_URL}")" \
        "${BASE_IMAGE_URL}"
fi

say "staging the working tree"
# The image installs from this copy rather than cloning from GitHub, so
# what you built is exactly what you are holding.
STAGE="${HERE}/src/modules/plate197/filesystem/opt/earshot"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"
for item in earshot deploy index-7inch.html backlight.py pyproject.toml README.md LICENSE; do
    [ -e "${PROJECT}/${item}" ] && cp -r "${PROJECT}/${item}" "${STAGE}/"
done
[ -d "${PROJECT}/plates" ] && cp -r "${PROJECT}/plates" "${STAGE}/"
find "${STAGE}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

say "building"
cd "${HERE}/src"
sudo -E ./build_dist

say "packaging"
# build_dist only mounts, customises and unmounts the image -- it leaves
# the result sitting in workspace/ under the *base* image's own filename,
# still root-owned from the chroot. CustomPiOS's own `release` script does
# the rename-and-zip step, but it isn't run automatically, and it needs
# the same DIST_PATH/CUSTOM_PI_OS_PATH environment build_dist set up for
# the actual build -- reconstructed here rather than hand-rolling the
# packaging logic ourselves.
export DIST_PATH="${HERE}/src"
# Assigned before export on purpose: `export VAR="$(cat ...)"` takes the
# exit status of *export*, so a missing custompios_path would leave this
# empty and send the next line off to run /release.
CUSTOM_PI_OS_PATH="$(cat "${HERE}/src/custompios_path")"
export CUSTOM_PI_OS_PATH
export PATH="${PATH}:${CUSTOM_PI_OS_PATH}"
sudo -E "${CUSTOM_PI_OS_PATH}/release" --sha256 || \
    echo "packaging step failed -- the raw image is still in ${HERE}/src/workspace/"

say "done"
ls -lh "${HERE}/src/workspace"/*.img "${HERE}/src/workspace"/*.zip \
    "${HERE}/src/workspace"/*.sha256 2>/dev/null || \
    echo "look in ${HERE}/src/workspace"

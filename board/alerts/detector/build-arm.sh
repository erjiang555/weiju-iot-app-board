#!/bin/bash
set -eo pipefail
: "${WEIJU_DETECTOR_BUILD_ROOT:?Set WEIJU_DETECTOR_BUILD_ROOT to the detector build directory}"
TASK_ROOT=$WEIJU_DETECTOR_BUILD_ROOT
mkdir -p "$TASK_ROOT"/logs "$TASK_ROOT"/artifacts
exec > "$TASK_ROOT/logs/build.log" 2>&1
trap 'result=$?; echo "$result" > "$TASK_ROOT/logs/build.exit"; exit "$result"' EXIT
cd "$TASK_ROOT"
tar -xzf sources.tar.gz
mkdir -p ncnn
tar -xzf source/references/ncnn-20240410.tar.gz -C ncnn --strip-components=1
cmake -S ncnn -B build-ncnn \
  -DCMAKE_TOOLCHAIN_FILE="$TASK_ROOT/source/person_detector/arm-toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$TASK_ROOT/ncnn-install" \
  -DNCNN_VULKAN=OFF -DNCNN_OPENMP=OFF -DNCNN_RUNTIME_CPU=OFF \
  -DNCNN_BUILD_TOOLS=OFF -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_BENCHMARK=OFF -DNCNN_BUILD_TESTS=OFF \
  -DNCNN_ARM82=OFF -DNCNN_ARM82DOT=OFF -DNCNN_ARM82FP16FML=OFF
cmake --build build-ncnn -j3
cmake --install build-ncnn
cmake -S source/person_detector -B build-person \
  -DCMAKE_TOOLCHAIN_FILE="$TASK_ROOT/source/person_detector/arm-toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release -Dncnn_DIR="$TASK_ROOT/ncnn-install/lib/cmake/ncnn"
cmake --build build-person -j3
cp build-person/person_detector artifacts/
cp source/references/mobilenet_ssd_voc_ncnn.param source/references/mobilenet_ssd_voc_ncnn.bin artifacts/
file artifacts/person_detector
sha256sum artifacts/* > artifacts/SHA256SUMS.txt
echo PERSON_DETECTOR_BUILD_COMPLETE

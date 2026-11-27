#!/usr/bin/env bash
# Reproducible phase benchmark harness for Protonida (GTA VI on Linux/Wine/vkd3d-proton).
# Usage: scripts/bench-phase.sh <profile_name> <duration_sec> [extra_env_key=val ...]
set -euo pipefail

PROFILE="${1:?usage: bench-phase.sh <profile> <duration_sec> [KEY=VAL ...]}"
DURATION="${2:?usage: bench-phase.sh <profile> <duration_sec> [KEY=VAL ...]}"
shift 2

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
XCL_HOME="${XCL_LINUX_HOME:-$HOME/.local/share/xcl-linux}"
VICTORY_EXE="$REPO_ROOT/target/x86_64-pc-windows-gnullvm/release/victory.exe"
GAME_EXE="${GAME_EXE:-Z:\\mnt\\NVMe\\GTAXBOX\\GTA6.Xbox.v1.0.2.1-CYBERLEEK\\game_scarlett_master.exe}"
OUT_DIR="/tmp/protonida-bench/${PROFILE}"
mkdir -p "$OUT_DIR"

LOG_FILE="$OUT_DIR/run.log"
DIAG_CSV="$OUT_DIR/frames.csv"
NVSMI_CSV="$OUT_DIR/nvidia-smi.csv"
rm -f "$LOG_FILE" "$DIAG_CSV" "$NVSMI_CSV"

# Base launcher-matching environment (720p Series S, RTX Low default)
export XCL_LINUX_HOME="$XCL_HOME"
export WINEARCH=win64
export WINEPREFIX="$XCL_HOME/prefix"
export WINESERVER="$XCL_HOME/wineserver-runtime/server/wineserver"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="d3d12,d3d12core,d3d11,dxgi,d3dcompiler_47=n;mscoree,mshtml="
export __GL_SHADER_DISK_CACHE_SIZE=4294967296
export __NV_PRIME_RENDER_OFFLOAD=1
export __VK_LAYER_NV_optimus=NVIDIA_only
export XCL_SAFE_BARRIER=0
export XCL_DXC_DIR="Z:${XCL_HOME//\//\\}\\dxc\\bin\\x64"
export WINEPATH="Z:${XCL_HOME//\//\\}\\llvm-mingw\\x86_64-w64-mingw32\\bin${WINEPATH:+;$WINEPATH}"
export VKD3D_SHADER_CACHE_PATH="$XCL_HOME/vkd3d-cache"
export DXVK_STATE_CACHE_PATH="$XCL_HOME/vkd3d-cache"
export VICTORY_LOG=info
export RUST_LOG=info
export DXVK_FILTER_DEVICE_NAME=NVIDIA
export VKD3D_FILTER_DEVICE_NAME=NVIDIA
export VICTORY_GAME_DIR='Z:\mnt\NVMe\GTAXBOX\GTA6.Xbox.v1.0.2.1-CYBERLEEK'
export VICTORY_OUTPUT=1280x720
export VICTORY_UPSCALER=6
export VICTORY_PORT_CONSTANTS=1
export VICTORY_PORT_DESCRIPTORS=1
export VICTORY_PORT_DESCRIPTORS_EVERY=32
export VICTORY_PORT_DESCRIPTORS_CHECK=2
export VICTORY_PORT_GUARD=1
export VICTORY_PORT_JOURNAL=1
export VICTORY_LATE_WAIT=3
export VICTORY_LATE_WAIT_LISTS=1024
export VICTORY_ASYNC_SUBMIT=1
export VICTORY_INPLACE=1
export VICTORY_INPLACE_MB=4096
export VICTORY_PLANES=1
export VICTORY_TEXTURE_BUDGET_MB=1024
export VICTORY_TEXTURE_STAGE_MB=64
export VICTORY_CARD_ROOM_FOR_TARGETS_MB=1536
export VICTORY_CARD_BUFFERS_MIN_KB=64
export VICTORY_CARD_BUFFERS_MB=256
export VICTORY_DYNAMIC_SLOTS=499999
export VICTORY_LEVELS_ON_DEMAND=1
export VICTORY_PIPELINE_WORKERS=4
export VICTORY_PIPELINE_WAIT_MS=0
export VICTORY_RELAY=2
export VICTORY_RELAY_COPY=1
export VICTORY_WIDE_BACK=1
export VICTORY_CARD_REHASH_RUNS=1
export VICTORY_JOIN_COPIES=1
export VICTORY_INDIRECT_WINDOW=0
export VICTORY_INDIRECT_BATCH=2
export VICTORY_CARD_STEPS=2
export VICTORY_MAP_THREADS=1
export VICTORY_CARD_AWAKE=1
export VICTORY_SHOW_AFTER=1
export VICTORY_LAND_SKIP=1
export VICTORY_GUARD_REST=50000
export VICTORY_GUARD_HOT=1000
export VICTORY_TEXTURE_NO_WAIT=0
export VICTORY_THIN=1
export VICTORY_JOIN_PARTS=8
export VICTORY_UPLOAD_AHEAD=1
export VICTORY_AMBIENT_AIR=1.5
export VICTORY_SKIP_PIPELINES="7115e46a79c78f98,59b1ba494f18c894,b8ca32a6899b87fb,659fe9cae59dbd88"
export VICTORY_BVH_BUILDER=1
export VICTORY_SKIP_RT=0
export VICTORY_AMBIENT=off
export VICTORY_AMBIENT_RT="0.35,2.5,0.8"
export VICTORY_DESCRIPTOR_STATS=1
export VICTORY_FRAME_DIAG="Z:${DIAG_CSV//\//\\}"
export VICTORY_KEYS="${VICTORY_KEYS:-25:Enter,40:Enter,55:Enter,70:Enter,85:Enter,100:Enter}"

# Apply profile-specific overrides
for kv in "$@"; do
    export "$kv"
done

# Start nvidia-smi telemetry in background
nvidia-smi \
  --query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,memory.total,clocks.gr,clocks.mem,power.draw,temperature.gpu \
  --format=csv -l 1 > "$NVSMI_CSV" 2>/dev/null &
NVSMI_PID=$!
trap 'kill "$NVSMI_PID" 2>/dev/null || true' EXIT

echo "=== Running profile=$PROFILE duration=${DURATION}s ==="
timeout "${DURATION}s" /usr/bin/wine "$VICTORY_EXE" run --console series-s --execute "$GAME_EXE" </dev/null > "$LOG_FILE" 2>&1 || true

kill "$NVSMI_PID" 2>/dev/null || true
wait "$NVSMI_PID" 2>/dev/null || true

echo "=== Summary for $PROFILE ==="
grep -E "host wave lanes|host compute pipeline accepted|host pipeline refused|swapchain VSync|UFFD WP async" "$LOG_FILE" | head -n 15 || true
echo "--- Last 8 FPS lines ---"
grep -E "fps, .* ms/frame|frametime percentiles|the card's clock per frame|Descriptor/RootCBV per frame" "$LOG_FILE" | tail -n 16 || true
echo "--- CSV rows: $(wc -l < "$DIAG_CSV" 2>/dev/null || echo 0) (frames), $(wc -l < "$NVSMI_CSV" 2>/dev/null || echo 0) (nvsmi) ---"

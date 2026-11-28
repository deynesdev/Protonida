<div align="center">

# Protonida

**Xbox Series X|S compatibility layer tuned for Linux, Wine + `vkd3d-proton`, and 6 GB NVIDIA RTX GPUs**

[![Platform](https://img.shields.io/badge/Platform-Linux%20(Wine%20%2B%20vkd3d--proton)-informational?style=flat-square&logo=linux)](https://github.com/deynesdev/Protonida)
[![GPU](https://img.shields.io/badge/Target-NVIDIA%20RTX%206GB%2B-76B900?style=flat-square&logo=nvidia)](https://github.com/deynesdev/Protonida)
[![Rust](https://img.shields.io/badge/Built%20with-Rust%20(x86--64--v3%20%2B%20Thin%20LTO)-orange?style=flat-square&logo=rust)](https://www.rust-lang.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0--or--later-blue?style=flat-square)](LICENSE)

*Native x64 execution • RDNA 2 → DXIL shader translation • Hardware Ray-Traced GI & Reflections • Zero CPU emulation*

</div>

---

## Overview

**Protonida** runs the Xbox Series X|S version of one game on a Linux PC. It does not emulate the console's CPU: the game's own x64 code runs under Wine, and its graphics calls go to `vkd3d-proton` (D3D12 on Vulkan) after the console's RDNA 2 shaders are translated to DXIL. It is a standalone, Linux-first fork of [VIctory](https://github.com/Marvisak/VIctory), aimed at **6 GB NVIDIA laptop GPUs**.

The goal is Series S picture quality at a steady 30 FPS. **It is far from that today** (about 5 FPS, see below). This is a work in progress for people who want to help, not a way to play the game yet.

> **Disclaimer:** This repository contains **no game files, binaries, keys, or assets**. You must supply your own legally obtained files. Protonida is not affiliated with or endorsed by any game developer, publisher, or console manufacturer.

---

## Current status

Everything below was measured on one machine: Fedora 44 (Linux 7.2.15, `/dev/ntsync`), Ryzen 5 7535HS (6 cores), 30 GB RAM, **RTX 4050 Laptop 6 GB** (driver 580.196.07), Wine 11.21 Staging with a patched `wineserver`, `vkd3d-proton` master (`v3.1-97-g71cae09`), DXVK 3.1.1, Series S profile. Scene: the game's opening prologue (the prison cell), measured with scripted runs.

**What works (2026-11-28)**
- The game boots, shows its menus and plays the opening prologue: cutscenes, then walking in the cell.
- **Character faces render.** They used to be missing in most runs; fixed by never dropping the head's one-time blend-shape pass ([docs/faces.md](docs/faces.md)).
- **NVIDIA DLSS works under Wine**: the game renders 1280x720 and DLSS outputs 1920x1080 ([setup](#2-gui-launcher-recommended)).
- **No more smearing when the camera moves**: the game's own upscaler is bypassed (render scale 1.0, the launcher default).
- **Second launches start much faster**: compiled GPU pipelines are now kept between launches.

**Performance**
- **4.5–5.2 FPS** in the cell, about 200 ms per frame, with or without DLSS.
- The GPU is the bottleneck (about 150 ms of each frame). Much of that time is shaders reading game data kept in system RAM instead of video memory; that is being worked on.

**Known problems**
- Very slow (see above). Not playable yet.
- The first time a scene is played, many draws are skipped for up to a minute while their GPU pipelines are compiled; `victory warm` (the launcher's Prebake) compiles all 67,000 ahead of time but takes about an hour.
- Memory use grows during a run (about 12 GB after a few minutes); keep other programs light. Run the game under a memory cap if you can.
- A crash in the game's own code (`+0x23cf683`) has been seen in older builds; not seen in the current one so far.
- DLSS frame generation is reported unsupported under Wine.
- Full 1920x1080 rendering (render scale 1.0 at 1080p) does not start; use DLSS for 1080p output.
- On Series S, `VICTORY_OUTPUT=1280x720` needs `VICTORY_UPSCALER=6` (the launcher sets it); the game's FidelityFX path divides by zero at that size.
- Distant billboard trees and props are skipped (`VICTORY_SKIP_PIPELINES`): they drew as broken flat panels.

Earlier versions of this README showed 20–30 FPS tables. Those numbers were never reproduced on this machine and have been removed.

---

## What is in it

The list below describes the changes this fork carries over VIctory. Numbers in it come from the work that added each change and have not all been re-measured; [Current status](#current-status) is what was measured.

### Linux & Wine Engineering
- **Wine-native memory & write-watch tracking (`watch.rs`, `xmem.rs`)**: Tracks placeholder/watched reservations in-process to eliminate refused `GetWriteWatch` calls (`293 → 0`) and `VirtualQuery` hole scans (`6,705 → 14`), plus batched contiguous `VirtualProtect(..., PAGE_READWRITE)` unguarding (`LoverCatys c0a3a1ff`).
- **Wine hot-guard tuning (`VICTORY_GUARD_HOT=1000`, `VICTORY_GUARD_REST=50000`, `MANY = 65,536`)**: Cuts `wineserver` `VirtualProtect` IPC faults and guard refreshes without introducing stale-frame artifacts.
- **Opt-in Linux `UFFD_FEATURE_WP_ASYNC` + `PAGEMAP_SCAN` bridge (`native/protonida_uffd.c`, `VICTORY_UFFD_WP=1`)**: Experimental `LD_PRELOAD` bridge bypassing `wineserver` IPC (kept opt-in after benchmarking showed `VirtualProtect` + `PUBLISHED` is faster on multi-GiB `MEM_MAPPED` views).
- **Frametime percentile telemetry (`p50 / p95 / p99 / max`) & ring-buffer CSV (`VICTORY_FRAME_DIAG`)**: Logged every 5 seconds in `victory_gpu::host` alongside FPS, VRAM, descriptor stats (`VICTORY_DESCRIPTOR_STATS=1`), and optional async per-frame CSV output; plus opt-in swapchain VSync (`VICTORY_VSYNC=1` + `VKD3D_SWAPCHAIN_LATENCY_FRAMES=1`, eliminating 2D/Bink intro cadence spikes).
- **16 MB pipeline worker stacks**: Prevents silent Wine 2 MB default thread stack overflows during shader translation.
- **Portable static `libunwind` (`+crt-static`) + AVX2 (`x86-64-v3`) + Thin LTO (`codegen-units = 1`)**: Zero `llvm-mingw` runtime DLL dependencies (`libunwind.dll` eliminated) and maximum CPU throughput across hot copy, hash, and descriptor loops.

### NVIDIA 6 GB & Renderer Optimizations
- **Renderer Ports 1, 2, 6, and 7** (via `avatarmanz/VIctory-7900XTX`):
  - **Port 1 (`VICTORY_PORT_CONSTANTS=1`)**: Maps the 185 MB graphics heap in place via `OpenExistingHeapFromAddress` (0 host refusals on `vkd3d-proton 3.2.0`).
  - **Port 2 (`VICTORY_PORT_DESCRIPTORS=1`)**: Patches descriptor-table stores directly instead of scanning ~600,000 slots/frame.
  - **Port 6 (`VICTORY_PORT_GUARD=1`)**: Shared `RwLock` write guards with per-block claims and parallel refresh planning.
  - **Port 7 (`VICTORY_PORT_JOURNAL=1`)**: 64-way sharded journal locks + submit-time heap sync.
- **NVIDIA 1M-heap `CreateCommittedResource` storm fix**: Eliminated 19,761 unnecessary per-view buffer allocations (`OWN_MADE: 19,761 → 2`, saving ~230 ms/frame).
- **Pooled `SHARED_SPARE` uploads & `WIDE_JOB` (256 KiB)** (`leonida c6c9da9f`): Reuses L0 `WRITE_BACK` upload buffers up to 8 MiB and cuts copy/shadow overhead (`copies` 23.7–28.9 ms → ~19.4 ms).
- **Configurable capped-heap dynamic slots (`VICTORY_DYNAMIC_SLOTS`)**: Safely bounds dynamic slots on NVIDIA's 1M-capped view heap (default `262,144` preserves static slots `0..737,855`).
- **`BufferSweep` span cache & parallel texture readbacks** (`XaviFortes/VIctory-RDNA`) + safe `VICTORY_INDIRECT_WINDOW=0` (`Itsbatt 340a8f8`).
- **Wine hot-block table expansion (`MANY = 65,536`)** — prevents hot-guard table wipes during streaming.
- **NVIDIA dynamic descriptor word-search & early slot release** (`avatarmanz` PR #237).
- **Optional RT pass skipping (`VICTORY_SKIP_RT=1`)**: Skips 15 unused ray-tracing compute passes and their VRAM allocations when running in non-RT mode.

### Series S Graphics & Shader Correctness
- **NVIDIA DLSS on Linux (`VICTORY_DLSS=1`, launcher: "NVIDIA DLSS")**: DLSS Super Resolution replaces the game's FidelityFX upscale: the game renders 1280x720 and DLSS Quality outputs 1920x1080, at the same frame rate as the plain 1280x720 path (4.6-5.0 fps in the opening scene, RTX 4050, 2026-11-28; DLSS took every upscale, none failed). NVIDIA's Streamline loads under Wine 11 with its signatures verified, through vkd3d-proton and dxvk-nvapi. Ported from [trustno1-coder/VIctory-NVIDIA](https://github.com/trustno1-coder/VIctory-NVIDIA) (Super Resolution only; frame generation is reported unsupported under Wine). Setup below.
- **Sharp picture instead of smearing (`VICTORY_RENDER_SCALE=1.0`, launcher default)**: The game's own upscaler renders at 2/3 of the output (853x480 for 1280x720) and blends earlier frames in, which at these frame rates smears the picture whenever the camera moves. With the render scale at 1.0 the game renders at the output size: in the opening scene's cell, turning the character keeps the fence, bunks and background visible instead of a milky blur, at the same frame rate (4.3 fps in both, RTX 4050, 2026-11-28). 1920x1080 at 1.0 does not start (the game's Series S back-buffer pool is too small for it); 1280x720 at 1.0 is the sharpest setting that runs.
- **Pipelines kept between launches (pipeline library fix)**: No pipeline library was ever made on vkd3d-proton, so every launch built every pipeline again ("no host pipeline library: pipelines are not kept"). The empty library was asked for with a zero-length blob whose pointer was not null, which vkd3d-proton refuses (`E_INVALIDARG`); Windows' D3D12 accepts it. It is now asked for with a null blob; the library is saved while playing and loaded at the next launch, and the game starts noticeably faster from the second launch on (2026-11-28).
- **Character heads drawn in every run (`VICTORY_PIPELINE_MUST`, default `61e54e90adccaf6a`)**: The head's blend-shape mapping is dispatched once per character; a dispatch whose pipeline was still being built was dropped, so in most runs the close-up heads never got built (eyes, teeth and hair floated over the collar). That pipeline is now built at first use and never dropped; heads were whole in 3 runs of 3 on the RTX 4050 (2026-11-28). The idea is from Vice-Tay #372. `VICTORY_PIPELINE_MUST=0` turns it off; a comma-separated hash list replaces the default. See [docs/faces.md](docs/faces.md).
- **On-demand mip streaming (`VICTORY_LEVELS_ON_DEMAND=1`, `VICTORY_TEXTURE_BUDGET_MB=1280`, `VICTORY_TEXTURE_NO_WAIT=0`)**: Prevents saturating a 512 MB up-front mip budget and avoids skipping unfilled remapped pages, increasing streamed textures `4.5x` (`417 MB → 1,877 MB` / `9,414` textures in the opening sequence, restoring crisp character uniform and environment textures).
- **GPU compute texture detiling (`VICTORY_GPU_DETILE=1`, `fxceless 3461f52f`), `TilingCache` readback reuse (`TaynanGT f0019f2e`), and zero-alloc `relay_merge` (`fxceless` PR `#1427`)**: Opt-in GPU compute detiling offloads texture detiling completely (`0.00 ms` CPU detile, `0` refused dispatches on RTX 4050 + `vkd3d-proton 3.2.0`) while preserving the `StagingPiece` pool when off (`default = 0`).
- **Full 16-bit `half_views` translator & cache stack (`LoverCatys 4b5856a1`, `shader_cache::FORMAT = 3`)**: Adds `emit/halves.rs`, `HostShader::half_views`, `descriptor::host::half_view`/`half_inexact`, and `TablePlan::half` alongside `ForceEarlyDepthStencil` (`VICTORY_EARLY_DEPTH`), raw 16-bit integer `d16` image loads (`VICTORY_D16_BITS`), `VICTORY_SPILL_STRICT` + `s_bfm_b32` literal folding, `v_fmac_f32` VOP3 bit-2 accumulator modifiers, and `wave.rs` unmatched `s_*saveexec` scanning.
- **Native Hardware Ray Tracing (`VICTORY_BVH_BUILDER=1`) + RT indoor ambient band (`VICTORY_AMBIENT_RT`)** (`avatarmanz 77137b98`): All 53 BVH structure builder pipelines run natively on NVIDIA RTX via `vkd3d-proton`, while `VICTORY_AMBIENT_RT=0.35,2.5,0.8` prevents crushed black indoor rooms without washing out RTGI detail.
- **Renderer Port 1 indirect ordering (`VICTORY_PORT_CONSTANTS_ORDER`)** (`avatarmanz 631400f5..15c3d0ea`): Clamps in-place indirect arguments right before execution to eliminate tree/palm and lamp-lit room flicker.
- **Open water without dark blocks (`victorynv 35c15608`) & slow-compute thinning (`VICTORY_THIN_SLOW_COMPUTE=4`, `LoverCatys d843417d`)**: Skips wave-wide water tiled-light shaders (`5dc3b25986622949`, `6f225b641c5b0708`) on 32-lane NVIDIA hosts (`VICTORY_WATER_LIGHT=1` re-enables) and thins `02384a60fda6f2d0` to every 4th frame, cutting GPU wait from 68–77 ms down to 11–26 ms.
- **Placed RT BVH `view_address` (`fxceless 9a9091d1`), `HostHeap::id` guard (`fxceless #1324`), shadow range reuse (`Vice-Tay 876a02f8`), and RT-off local-lights skip (`VIctory-DXR d5efb363`)**: Fixes placed acceleration-structure view addresses, prevents dynamic slot release into reused heap addresses, eliminates per-frame shadow span `Vec` churn, and caps `list::thin` backing lists at 64.
- **DirectStorage black-face fix (`VICTORY_PAGE_WRITES=3`), dynamic table `VIEW_INTEGER` (`HostHeap::dynamic_word`), and split-submission slot release** (`fxceless e4245f3c` / `#1320`, `flamingo a1178ce8`): Re-queues texture slots under DirectStorage writes, populates `self.strides` for dynamic table slots, and releases split-submission descriptor slots.
- **Targeted Wave32 compute auto-refresh (`host_shader_fresh`) & `HostList::untouched()` upload guard** (`leonida dd306a64`): Automatically re-translates stale refused Wave64 compute pipelines (e.g. `6871d6e4b3ac9a10`) without invalidating the shader cache, and guards `plane_uploads`/`shared_uploads` while keeping pooled uploads + Kelvaroq 6 GB VRAM budgets (`VICTORY_CARD_BUFFERS_MB=256`, `VICTORY_TEXTURE_STAGE_MB=64`, `VICTORY_TEXTURE_BUDGET_MB=512`).
- **Wave32 loose-LDS & `ds_bpermute_b32` lowering** (`leonida 76c6f04c`): Lowers Wave64 compute shaders that read/write LDS across divergent branches to NVIDIA Wave32 cleanly, plus `CalculateLOD` coordinate fix.
- **BC7 texture rotation fix** (`fxceless` `06702fe` / upstream #3111).
- **RDNA 2 fused `v_mad_legacy_f32`** with FTZ & zero-factor semantics (`fxceless` `f6a09d3` / PR #1026).
- **SDWA 16-bit sign extension** (`fxceless` `58de9bf` / upstream #3124).
- **Distant billboard impostor skip (`VICTORY_SKIP_PIPELINES`)**: Ungated in release builds to remove broken flat distant panels.

---

## Quick Start (Linux)

### 1. Cross-compile from Linux
Requires Rust with the `x86_64-pc-windows-gnullvm` target and an [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) toolchain. Rust links this target with `x86_64-w64-mingw32-clang`, so that binary must be on `PATH` (Fedora's `clang`/`lld` packages do not provide it). The xcl-linux setup already ships one under `$XCL_LINUX_HOME/llvm-mingw`:

```bash
export PATH="${XCL_LINUX_HOME:-$HOME/.local/share/xcl-linux}/llvm-mingw/bin:$PATH"
cargo build --release --target x86_64-pc-windows-gnullvm -p victory
```

`error: linker 'x86_64-w64-mingw32-clang' not found` means that directory is not on `PATH`. Alternatively, point Cargo at the linker directly with `CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER=/path/to/llvm-mingw/bin/x86_64-w64-mingw32-clang`.

### 2. GUI Launcher (Recommended)
Run the built-in GTK4 launcher (requires system Python 3 + PyGObject/GTK4, preinstalled on Fedora/GNOME):

```bash
/usr/bin/python3 ./scripts/protonida-launcher.py
```

Run it with the system Python (`/usr/bin/python3`), which has the GTK 4 bindings; a Homebrew or other `python3` first on `PATH` fails with `No module named 'gi'`. Set **Advanced → victory.exe Path** to your build. **Stop** ends the whole game process group (SIGKILL after 5 s if needed), and the launcher notices when the game exits on its own.

**DLSS setup (once).** Nothing NVIDIA ships is in this repository.
1. NVIDIA Streamline SDK 2.14.16 (NVIDIA RTX SDK licence) from <https://github.com/NVIDIA-RTX/Streamline/releases>: put `bin/x64` (`sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll`, `nvngx_dlss.dll`, ...) in `~/.local/share/xcl-linux/nvidia-sdk/streamline-v2.14.16/bin/x64`, or set the launcher's Streamline folder.
2. The Wine prefix as Proton sets it up for NGX: dxvk-nvapi's `nvapi64.dll` (e.g. from GE-Proton's `files/lib/wine/nvapi/x86_64-windows`) and the driver's `nvngx.dll` and `_nvngx.dll` (`/usr/lib64/nvidia/wine`) in `drive_c/windows/system32`, and `wine reg add 'HKLM\SOFTWARE\NVIDIA Corporation\Global\NGXCore' /v FullPath /t REG_SZ /d 'C:\windows\system32' /f`.
3. Check without the game: `victory.exe upscaler-check --device` (with `VICTORY_STREAMLINE_DIR`, `DXVK_ENABLE_NVAPI=1`, `nvapi64=n` in `WINEDLLOVERRIDES`) must print "DLSS ran on the GPU".
4. Tick "NVIDIA DLSS" in the launcher.

Provides main controls for **Resolution** (720p / 1080p / 1440p / 4K), **Console Profile** (Series S Lockhart vs Series X Anaconda), **Ray Tracing (RTX)** (auto-syncing `VICTORY_BVH_BUILDER` and `settings.xml`), **MangoHud**, a RAM-guarded **Prebake Shaders** button (`victory shaders` → `victory warm` with `VICTORY_PREBAKE_BIG_MAX=2` + `/proc/meminfo` watchdog), and a collapsible **Advanced** drawer for all `VICTORY_*`, Wine, VRAM, guard, experimental UFFD WP, and skip-pipeline settings (saved to `~/.config/protonida/launcher.json`).

### 3. CLI Launch Profiles

For measurements use `scripts/bench-phase.sh <profile> <seconds> [KEY=VAL ...]` (results in `/tmp/protonida-bench/<profile>/`). It takes the game from `GAME_EXE` (a Linux or Wine path) or from the launcher's saved choice, and the build from `BENCH_EXE` if set.

#### Series S Hardware RTX (native RTGI & reflections)
```bash
export WINEDLLOVERRIDES="d3d12,d3d12core,d3d11,dxgi,d3dcompiler_47=n;mscoree,mshtml="
export __GL_SHADER_DISK_CACHE_SIZE=4294967296

export VICTORY_OUTPUT=1280x720
export VICTORY_UPSCALER=6
export VICTORY_PORT_CONSTANTS=1 VICTORY_PORT_DESCRIPTORS=1 VICTORY_PORT_GUARD=1 VICTORY_PORT_JOURNAL=1
export VICTORY_PORT_DESCRIPTORS_EVERY=32 VICTORY_PORT_DESCRIPTORS_CHECK=2
export VICTORY_LATE_WAIT=3 VICTORY_LATE_WAIT_LISTS=1024 VICTORY_ASYNC_SUBMIT=1
export VICTORY_INPLACE=1 VICTORY_INPLACE_MB=4096
export VICTORY_GUARD_REST=50000 VICTORY_GUARD_HOT=1000
export VICTORY_INDIRECT_WINDOW=0 VICTORY_SHOW_AFTER=0
export VICTORY_TEXTURE_BUDGET_MB=768 VICTORY_CARD_ROOM_FOR_TARGETS_MB=2304
export VICTORY_CARD_BUFFERS_MIN_KB=64 VICTORY_CARD_BUFFERS_MB=1024
export VICTORY_PIPELINE_WORKERS=2 VICTORY_PIPELINE_WAIT_MS=0

# Native hardware ray tracing
export VICTORY_BVH_BUILDER=1
export VICTORY_SKIP_RT=0
export VICTORY_AMBIENT=off

wine target/x86_64-pc-windows-gnullvm/release/victory.exe run \
  --console series-s \
  --execute 'Z:\path\to\game_scarlett_master.exe'
```

#### Series S without ray tracing (Screen-Space SH GI)
Swap the last three variables:
```bash
export VICTORY_BVH_BUILDER=0
export VICTORY_SKIP_RT=1
export VICTORY_AMBIENT=2.0
export VICTORY_AMBIENT_AIR=1.5
```

> **Tip for 6 GB RTX cards:** In the title's `settings.xml`, setting `rtDynamicShadows="false"` and `ssroSampleCount="1"` keeps full hardware RTGI and RT reflections while eliminating heavy shadow-ray overhead.

---

## Credits & Upstream Projects

Protonida stands on the shoulders of an incredible open-source community:
- **[Marvisak/VIctory](https://github.com/Marvisak/VIctory)** — original compatibility layer and RDNA 2 → DXIL shader translator
- **[avatarmanz/VIctory-7900XTX](https://github.com/avatarmanz/VIctory-7900XTX)** — Renderer Ports 1–7, `VICTORY_OUTPUT`, frame-radiance `ambient.rs`, NVIDIA Wave32 & PR #237
- **[fxceless/victory-fork](https://github.com/fxceless/victory-fork)** — BC7 rotation fix, RDNA 2 `v_mad_legacy_f32`, SDWA sign extension, flight-room & diagnostics engineering
- **[XaviFortes/VIctory-RDNA](https://github.com/XaviFortes/VIctory-RDNA)** — `BufferSweep` span caching & parallel texture readbacks
- **[LoverCatys/ruff-lab](https://git.gtahub.st/github/LoverCatys--ruff-lab)** & **[Belobug/VIctory-Belobug-AMD](https://github.com/Belobug/VIctory-Belobug-AMD)** — `VICTORY_SKIP_RT`, `x86-64-v3` + thin LTO profile, and ambient lighting research
- **[trustno1-coder/VIctory-NVIDIA](https://github.com/trustno1-coder/VIctory-NVIDIA)** — DLSS Super Resolution through NVIDIA Streamline (`streamline.rs`, the FidelityFX interposer)
- **[TaynanGT/Vice-Tay](https://git.gtahub.st/github/TaynanGT--Vice-Tay)** (#372, via LoverCatys/ruff-lab) — the idea that the head blend-shape pipeline must not be dropped
- **[realblastex/flamingo](https://git.gtahub.st/github/realblastex--flamingo)** — the character-geometry investigation that narrowed down the missing heads
- **[HansKristian-Work/vkd3d-proton](https://github.com/HansKristian-Work/vkd3d-proton)** — D3D12-on-Vulkan translation on Linux

---

## License

Licensed under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). See [LICENSE](LICENSE) for details.

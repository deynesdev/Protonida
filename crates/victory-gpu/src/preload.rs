//! Scene preload gate and monotonic title-clock freeze during texture/PSO prewarming.
//!
//! When the game leaves its loading screen and enters the prologue (or switches
//! scenes with a large streaming burst), hundreds/thousands of textures and
//! newly-bound pipelines can still be streaming in from HDD or compiling in
//! the background. Without a preload gate, the opening frames render with
//! missing ground/meshes/textures while wall-clock time keeps advancing and
//! skips through the prologue cutscene.
//!
//! Controls (all ON by default unless disabled):
//! - `VICTORY_PRELOAD_TEXTURES=0` disables the preload gate.
//! - `VICTORY_PRELOAD_TIMEOUT_MS` (default `20000`): max time spent draining
//!   textures + in-flight DirectStorage I/O + graphics PSOs before frame 1 of
//!   the world (clamped `0..=60000`).
//! - `VICTORY_SCENE_PRELOAD_MS` (default `4000`): max time spent draining a
//!   mid-run streaming burst (`due >= 256` textures) before rendering the frame
//!   (clamped `0..=20000`).
//! - `VICTORY_PRELOAD_FREEZE_CLOCK=0` disables pausing title `QueryPerformanceCounter`
//!   and `GetTickCount64` while the preload gate is active.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

/// Whether the preload gate is enabled (`VICTORY_PRELOAD_TEXTURES`, default ON).
pub fn enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("VICTORY_PRELOAD_TEXTURES").is_none_or(|v| v != "0"))
}

/// Whether the title's clocks pause while preloading (`VICTORY_PRELOAD_FREEZE_CLOCK`, default ON).
pub fn freeze_clock_enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| {
        std::env::var_os("VICTORY_PRELOAD_FREEZE_CLOCK").is_none_or(|v| v != "0")
    })
}

/// Maximum duration of the first-world-frame preload gate (`VICTORY_PRELOAD_TIMEOUT_MS`).
pub fn world_timeout() -> Duration {
    static MS: OnceLock<u64> = OnceLock::new();
    let ms = *MS.get_or_init(|| {
        std::env::var("VICTORY_PRELOAD_TIMEOUT_MS")
            .ok()
            .and_then(|v| v.trim().parse::<u64>().ok())
            .unwrap_or(20_000)
            .min(60_000)
    });
    Duration::from_millis(ms)
}

/// Maximum duration of a mid-run scene burst drain (`VICTORY_SCENE_PRELOAD_MS`).
pub fn scene_timeout() -> Duration {
    static MS: OnceLock<u64> = OnceLock::new();
    let ms = *MS.get_or_init(|| {
        std::env::var("VICTORY_SCENE_PRELOAD_MS")
            .ok()
            .and_then(|v| v.trim().parse::<u64>().ok())
            .unwrap_or(4_000)
            .min(20_000)
    });
    Duration::from_millis(ms)
}

static WORLD_ENTERED: AtomicBool = AtomicBool::new(false);

/// Marks the first world frame (`heap_buffers`). Returns `true` only once.
pub fn mark_world_entered() -> bool {
    !WORLD_ENTERED.swap(true, Ordering::AcqRel)
}

/// Whether the title has entered the 3D world (left the loading screen).
pub fn world_entered() -> bool {
    WORLD_ENTERED.load(Ordering::Acquire)
}

static IO_PENDING: OnceLock<fn() -> usize> = OnceLock::new();

/// Registers a callback (from `victory-hle::modules::dstorage`) reporting
/// submitted / in-flight DirectStorage read requests.
pub fn register_io_pending(f: fn() -> usize) {
    let _ = IO_PENDING.set(f);
}

/// Number of DirectStorage reads currently queued or running, if registered.
pub fn io_pending() -> usize {
    IO_PENDING.get().map_or(0, |f| f())
}

/// Monotonic virtual-clock tracker that subtracts paused intervals so title
/// timers never jump forward across a preload stall and never step backward.
#[derive(Default)]
struct ClockTracker {
    paused_at: Option<u64>,
    total_paused: u64,
    last_returned: u64,
}

impl ClockTracker {
    fn enter(&mut self, raw: u64) {
        if self.paused_at.is_none() {
            self.paused_at = Some(raw);
        }
    }

    fn leave(&mut self, raw: u64) {
        if let Some(since) = self.paused_at.take() {
            self.total_paused = self.total_paused.saturating_add(raw.saturating_sub(since));
        }
    }

    fn read(&mut self, raw: u64) -> u64 {
        let effective = self.paused_at.unwrap_or(raw);
        let adjusted = effective.saturating_sub(self.total_paused);
        if adjusted > self.last_returned {
            self.last_returned = adjusted;
        }
        self.last_returned
    }
}

/// Fast-path flag: stays `false` until the first clock freeze happens so normal
/// QPC/GetTickCount64 calls pay only a single atomic load during loading.
static CLOCK_ENGAGED: AtomicBool = AtomicBool::new(false);
static FREEZE_DEPTH: AtomicU32 = AtomicU32::new(0);
static QPC_CLOCK: Mutex<ClockTracker> = Mutex::new(ClockTracker {
    paused_at: None,
    total_paused: 0,
    last_returned: 0,
});
static TICK64_CLOCK: Mutex<ClockTracker> = Mutex::new(ClockTracker {
    paused_at: None,
    total_paused: 0,
    last_returned: 0,
});

#[cfg(windows)]
fn raw_clocks() -> (u64, u64) {
    use windows_sys::Win32::System::Performance::QueryPerformanceCounter;
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;
    let mut qpc = 0i64;
    // SAFETY: plain OS clock queries into stack local.
    unsafe {
        QueryPerformanceCounter(&mut qpc);
        (qpc.max(0) as u64, GetTickCount64())
    }
}

#[cfg(not(windows))]
fn raw_clocks() -> (u64, u64) {
    (0, 0)
}

/// RAII guard that pauses title QPC and GetTickCount64 clocks while alive.
/// Supports nesting across threads; clocks resume only when the last guard drops.
pub struct FreezeGuard {
    active: bool,
}

/// Pauses the title's clocks while preloading (no-op if disabled via
/// `VICTORY_PRELOAD_FREEZE_CLOCK=0`).
pub fn freeze_clock() -> FreezeGuard {
    if !freeze_clock_enabled() {
        return FreezeGuard { active: false };
    }
    CLOCK_ENGAGED.store(true, Ordering::Release);
    if FREEZE_DEPTH.fetch_add(1, Ordering::AcqRel) == 0 {
        let (qpc, tick) = raw_clocks();
        QPC_CLOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .enter(qpc);
        TICK64_CLOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .enter(tick);
    }
    FreezeGuard { active: true }
}

impl Drop for FreezeGuard {
    fn drop(&mut self) {
        if !self.active {
            return;
        }
        if FREEZE_DEPTH.fetch_sub(1, Ordering::AcqRel) == 1 {
            let (qpc, tick) = raw_clocks();
            QPC_CLOCK
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .leave(qpc);
            TICK64_CLOCK
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .leave(tick);
        }
    }
}

/// Adjusts a raw `QueryPerformanceCounter` value by subtracting paused preload time.
pub fn adjusted_qpc(raw: i64) -> i64 {
    if !CLOCK_ENGAGED.load(Ordering::Acquire) {
        return raw;
    }
    let adjusted = QPC_CLOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .read(raw.max(0) as u64);
    adjusted.min(i64::MAX as u64) as i64
}

/// Adjusts a raw `GetTickCount64` value by subtracting paused preload time.
pub fn adjusted_tick64(raw: u64) -> u64 {
    if !CLOCK_ENGAGED.load(Ordering::Acquire) {
        return raw;
    }
    TICK64_CLOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .read(raw)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clock_tracker_freezes_resumes_smoothly_and_stays_monotonic() {
        let mut c = ClockTracker::default();
        assert_eq!(c.read(100), 100);
        assert_eq!(c.read(150), 150);

        // Freeze at 200; raw advances to 5200 (+5000 while preloading).
        c.enter(200);
        assert_eq!(c.read(200), 200);
        assert_eq!(c.read(1500), 200);
        assert_eq!(c.read(5200), 200);

        // Resume at 5200: title clock resumes cleanly at 200 with zero jump!
        c.leave(5200);
        assert_eq!(c.read(5200), 200);
        assert_eq!(c.read(5250), 250);

        // Second pause at 6000..8000 (+2000).
        c.enter(6000);
        assert_eq!(c.read(7500), 1000);
        c.leave(8000);
        assert_eq!(c.read(8000), 1000);
        assert_eq!(c.read(8100), 1100);

        // Even if a racing thread supplies an older raw reading, never goes backwards.
        assert_eq!(c.read(7900), 1100);
    }

    #[test]
    fn nested_enter_keeps_original_pause_start() {
        let mut c = ClockTracker::default();
        c.enter(1000);
        c.enter(1500); // must not overwrite paused_at
        assert_eq!(c.read(3000), 1000);
        c.leave(3000);
        assert_eq!(c.read(3100), 1100);
    }
}

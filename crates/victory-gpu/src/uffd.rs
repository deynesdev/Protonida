//! Linux `UFFD_FEATURE_WP_ASYNC` + `PAGEMAP_SCAN` (`PM_SCAN_WP_MATCHING`) bridge
//! loaded via `LD_PRELOAD=libprotonida_uffd.so` under Wine.
//!
//! Calls into the native Linux ELF shared library using `extern "sysv64"` so
//! syscalls go straight through `libc.so.6` to the Linux kernel with zero
//! `wineserver` IPC and without Wine's `PR_SET_SYSCALL_USER_DISPATCH`
//! intercepting raw PE `syscall` instructions.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{OnceLock, RwLock};

const PROTONIDA_UFFD_MAGIC: u64 = 0x5052_4F54_5546_4644; // "PROTUFFD"
const PROTONIDA_UFFD_VERSION: u32 = 1;
const KERNEL_PAGE: usize = 4096;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct UffdRange {
    pub start: u64,
    pub end: u64,
}

#[repr(C)]
struct ProtonidaUffdVtable {
    magic: u64,
    version: u32,
    ready: u32,
    features: u64,
    uffd_register: unsafe extern "sysv64" fn(u64, u64) -> i32,
    uffd_unregister: unsafe extern "sysv64" fn(u64, u64) -> i32,
    uffd_arm: unsafe extern "sysv64" fn(u64, u64) -> i32,
    uffd_scan_rearm: unsafe extern "sysv64" fn(u64, u64, *mut UffdRange, u32, u32) -> i64,
}

struct Bridge {
    vt: &'static ProtonidaUffdVtable,
    views_on: bool,
    stats_on: bool,
}

// SAFETY: vtable points to static read-only function pointers in libprotonida_uffd.so.
unsafe impl Send for Bridge {}
unsafe impl Sync for Bridge {}

static BRIDGE: OnceLock<Option<Bridge>> = OnceLock::new();

fn bridge() -> Option<&'static Bridge> {
    BRIDGE
        .get_or_init(|| {
            let wp_on = std::env::var("VICTORY_UFFD_WP").is_ok_and(|v| v.trim() == "1");
            if !wp_on {
                return None;
            }
            let raw = std::env::var("PROTONIDA_UFFD_VTABLE").ok()?;
            let trimmed = raw.trim().strip_prefix("0x").or_else(|| raw.trim().strip_prefix("0X")).unwrap_or(raw.trim());
            let addr = usize::from_str_radix(trimmed, 16).ok()?;
            if addr == 0 {
                return None;
            }
            // SAFETY: PROTONIDA_UFFD_VTABLE is published by libprotonida_uffd.so's constructor
            // in the same Wine process address space and lives for the process lifetime.
            let vt = unsafe { &*(addr as *const ProtonidaUffdVtable) };
            if vt.magic != PROTONIDA_UFFD_MAGIC || vt.version != PROTONIDA_UFFD_VERSION || vt.ready == 0 {
                tracing::warn!(
                    magic = format_args!("{:#x}", vt.magic),
                    version = vt.version,
                    ready = vt.ready,
                    "PROTONIDA_UFFD_VTABLE invalid or not ready; falling back to GetWriteWatch/VirtualProtect"
                );
                return None;
            }
            let views_on = std::env::var("VICTORY_UFFD_VIEWS").is_ok_and(|v| v.trim() == "1");
            let stats_on = std::env::var("VICTORY_UFFD_STATS").is_ok_and(|v| v.trim() == "1");
            tracing::info!(
                features = format_args!("{:#x}", vt.features),
                views = views_on,
                stats = stats_on,
                "Linux UFFD_FEATURE_WP_ASYNC + PAGEMAP_SCAN bridge active"
            );
            Some(Bridge { vt, views_on, stats_on })
        })
        .as_ref()
}

pub(crate) fn enabled() -> bool {
    bridge().is_some()
}

pub(crate) fn views_enabled() -> bool {
    bridge().is_some_and(|b| b.views_on)
}

pub(crate) fn stats_enabled() -> bool {
    bridge().is_some_and(|b| b.stats_on)
}

/// Sorted, non-overlapping byte intervals `[start, end)` registered with UFFD WP.
struct RangeSet {
    ranges: RwLock<Vec<(usize, usize)>>,
}

impl RangeSet {
    const fn new() -> Self {
        Self { ranges: RwLock::new(Vec::new()) }
    }

    fn insert(&self, start: usize, end: usize) {
        if start >= end {
            return;
        }
        let mut v = self.ranges.write().unwrap_or_else(|e| e.into_inner());
        let mut s = start;
        let mut e = end;
        let mut out = Vec::with_capacity(v.len() + 1);
        let mut inserted = false;
        for &(a, b) in v.iter() {
            if b < s {
                out.push((a, b));
            } else if e < a {
                if !inserted {
                    out.push((s, e));
                    inserted = true;
                }
                out.push((a, b));
            } else {
                s = s.min(a);
                e = e.max(b);
            }
        }
        if !inserted {
            out.push((s, e));
        }
        *v = out;
    }

    fn remove(&self, start: usize, end: usize) {
        if start >= end {
            return;
        }
        let mut v = self.ranges.write().unwrap_or_else(|e| e.into_inner());
        if v.is_empty() {
            return;
        }
        let mut out = Vec::with_capacity(v.len());
        for &(a, b) in v.iter() {
            if b <= start || a >= end {
                out.push((a, b));
            } else {
                if a < start {
                    out.push((a, start));
                }
                if b > end {
                    out.push((end, b));
                }
            }
        }
        *v = out;
    }

    fn contains(&self, start: usize, end: usize) -> bool {
        if start >= end {
            return false;
        }
        let v = self.ranges.read().unwrap_or_else(|e| e.into_inner());
        let idx = v.partition_point(|&(a, _)| a <= start);
        idx > 0 && v[idx - 1].1 >= end
    }

    fn snapshot(&self) -> Vec<(usize, usize)> {
        self.ranges.read().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

static PRIVATE_RANGES: RangeSet = RangeSet::new();
static VIEW_RANGES: RangeSet = RangeSet::new();

/// Telemetry counters (per-period swapped on frame log).
static SCANS: AtomicU64 = AtomicU64::new(0);
static SCAN_NS: AtomicU64 = AtomicU64::new(0);
static DIRTY_4K: AtomicU64 = AtomicU64::new(0);
static DIRTY_REGIONS: AtomicU64 = AtomicU64::new(0);
static FALLBACKS: AtomicU64 = AtomicU64::new(0);
static REG_OK: AtomicU64 = AtomicU64::new(0);
static REG_FAIL: AtomicU64 = AtomicU64::new(0);
static VIEW_SCANS: AtomicU64 = AtomicU64::new(0);

/// Optional shadow/texture hashing & upload telemetry (active when `VICTORY_UFFD_STATS=1`).
pub(crate) static HASH_NS: AtomicU64 = AtomicU64::new(0);
pub(crate) static UPLOAD_COPY_NS: AtomicU64 = AtomicU64::new(0);
pub(crate) static UPLOAD_BYTES: AtomicU64 = AtomicU64::new(0);

pub(crate) fn register_private(at: usize, len: usize) -> bool {
    let Some(b) = bridge() else {
        return false;
    };
    if len == 0 {
        return false;
    }
    let start = at & !(KERNEL_PAGE - 1);
    let end = at.saturating_add(len).div_ceil(KERNEL_PAGE) * KERNEL_PAGE;
    let rc = unsafe { (b.vt.uffd_register)(start as u64, (end - start) as u64) };
    if rc == 0 {
        PRIVATE_RANGES.insert(start, end);
        REG_OK.fetch_add(1, Ordering::Relaxed);
        true
    } else {
        REG_FAIL.fetch_add(1, Ordering::Relaxed);
        false
    }
}

pub(crate) fn register_view(at: usize, len: usize) -> bool {
    let Some(b) = bridge() else {
        return false;
    };
    if !b.views_on || len == 0 {
        return false;
    }
    let start = at & !(KERNEL_PAGE - 1);
    let end = at.saturating_add(len).div_ceil(KERNEL_PAGE) * KERNEL_PAGE;
    let rc = unsafe { (b.vt.uffd_register)(start as u64, (end - start) as u64) };
    if rc == 0 {
        VIEW_RANGES.insert(start, end);
        REG_OK.fetch_add(1, Ordering::Relaxed);
        true
    } else {
        REG_FAIL.fetch_add(1, Ordering::Relaxed);
        false
    }
}

pub(crate) fn unregister(at: usize, len: usize) {
    let Some(b) = bridge() else {
        return;
    };
    if len == 0 {
        return;
    }
    let start = at & !(KERNEL_PAGE - 1);
    let end = at.saturating_add(len).div_ceil(KERNEL_PAGE) * KERNEL_PAGE;
    PRIVATE_RANGES.remove(start, end);
    VIEW_RANGES.remove(start, end);
    let _ = unsafe { (b.vt.uffd_unregister)(start as u64, (end - start) as u64) };
}

pub(crate) fn is_private_registered(start: usize, end: usize) -> bool {
    bridge().is_some() && PRIVATE_RANGES.contains(start, end)
}

pub(crate) fn is_view_registered(start: usize, end: usize) -> bool {
    bridge().is_some_and(|b| b.views_on) && VIEW_RANGES.contains(start, end)
}

/// Sweeps ALL UFFD-registered `MEM_MAPPED` views in a handful of coalesced
/// `PAGEMAP_SCAN(PM_SCAN_WP_MATCHING)` ioctls. Returns:
/// - `covered`: 64 KiB page intervals `(first, last)` covered by the sweep
///   (to be inserted into `Journal::ahead` so all subsequent `writes()` /
///   `nothing_new()` calls in the submission hit the journal with 0 ioctls)
/// - `dirty`: `(page_64k, parts_16bit)` for every 4 KiB page written since the
///   last sweep across ANY view (including aliases).
pub(crate) fn sweep_all_views() -> Option<(Vec<(usize, usize)>, Vec<(usize, u16)>)> {
    if !views_enabled() {
        return None;
    }
    let raw = VIEW_RANGES.snapshot();
    if raw.is_empty() {
        return Some((Vec::new(), Vec::new()));
    }
    let page_64k = crate::watch::PAGE;
    let covered: Vec<(usize, usize)> = raw
        .iter()
        .map(|&(a, b)| (a / page_64k, b.div_ceil(page_64k)))
        .filter(|&(a, b)| a < b)
        .collect();
    // Coalesce intervals within 32 MiB (up to 512 MiB per ioctl) so the entire
    // guest address space is swept in ~5–20 PAGEMAP_SCAN ioctls.
    let mut dirty: Vec<(usize, u16)> = Vec::new();
    let mut i = 0;
    while i < raw.len() {
        let s = raw[i].0;
        let mut e = raw[i].1;
        let mut k = i + 1;
        while k < raw.len() && raw[k].0 <= e + (32 << 20) && (raw[k].1 - s) <= (512 << 20) {
            e = e.max(raw[k].1);
            k += 1;
        }
        if let Some(pages) = scan_pages_64k(s, e, true) {
            dirty.extend(pages);
        }
        i = k;
    }
    Some((covered, dirty))
}

thread_local! {
    static SCAN_BUF: std::cell::RefCell<Vec<UffdRange>> = std::cell::RefCell::new(Vec::with_capacity(4096));
}

/// Scans `[start, end)` (byte addresses, 4 KiB aligned) with `PAGEMAP_SCAN`
/// (`PM_SCAN_WP_MATCHING`), atomically re-arming WP on matched dirty pages,
/// and folds them into 64 KiB `(page, 16-bit 4 KiB parts)` entries.
///
/// Returns `None` if UFFD is unavailable or the kernel scan failed (caller
/// should fall back to `GetWriteWatch` / `VirtualProtect`).
pub(crate) fn scan_pages_64k(start: usize, end: usize, is_view: bool) -> Option<Vec<(usize, u16)>> {
    let b = bridge()?;
    if start >= end {
        return Some(Vec::new());
    }
    let page_64k = crate::watch::PAGE;
    let started = std::time::Instant::now();
    let res = SCAN_BUF.with(|buf| {
        let mut buf = buf.borrow_mut();
        if buf.len() < 4096 {
            buf.resize(4096, UffdRange::default());
        }
        loop {
            let cap = buf.len() as u32;
            // check_wpasync = 0 so sparse/uncommitted holes inside a reservation
            // do not abort the scan; only registered written pages have PAGE_IS_WRITTEN.
            let n = unsafe {
                (b.vt.uffd_scan_rearm)(start as u64, (end - start) as u64, buf.as_mut_ptr(), cap, 0)
            };
            if n == -(libc_enospc() as i64) {
                let next_len = buf.len().saturating_mul(2).min(262_144);
                if next_len == buf.len() {
                    return None;
                }
                buf.resize(next_len, UffdRange::default());
                continue;
            }
            if n < 0 {
                return None;
            }
            let count = n as usize;
            let mut dirty_4k = 0u64;
            let mut pages: Vec<(usize, u16)> = Vec::new();
            for r in &buf[..count] {
                let mut cur = (r.start as usize).max(start);
                let stop = (r.end as usize).min(end);
                while cur < stop {
                    dirty_4k += 1;
                    let page = cur / page_64k;
                    let part = 1u16 << ((cur % page_64k) / KERNEL_PAGE);
                    match pages.last_mut() {
                        Some((last, parts)) if *last == page => *parts |= part,
                        _ => pages.push((page, part)),
                    }
                    cur += KERNEL_PAGE;
                }
            }
            SCANS.fetch_add(1, Ordering::Relaxed);
            if is_view {
                VIEW_SCANS.fetch_add(1, Ordering::Relaxed);
            }
            DIRTY_4K.fetch_add(dirty_4k, Ordering::Relaxed);
            DIRTY_REGIONS.fetch_add(count as u64, Ordering::Relaxed);
            return Some(pages);
        }
    });
    SCAN_NS.fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
    if res.is_none() {
        FALLBACKS.fetch_add(1, Ordering::Relaxed);
    }
    res
}

#[inline]
const fn libc_enospc() -> i32 {
    28 // Linux ENOSPC
}

pub(crate) fn stats_line(frames: f64) {
    let Some(b) = bridge() else {
        return;
    };
    let scans = SCANS.swap(0, Ordering::Relaxed) as f64 / frames;
    let view_scans = VIEW_SCANS.swap(0, Ordering::Relaxed) as f64 / frames;
    let scan_ms = SCAN_NS.swap(0, Ordering::Relaxed) as f64 / 1e6 / frames;
    let dirty_4k = DIRTY_4K.swap(0, Ordering::Relaxed) as f64 / frames;
    let regions = DIRTY_REGIONS.swap(0, Ordering::Relaxed) as f64 / frames;
    let fallbacks = FALLBACKS.swap(0, Ordering::Relaxed) as f64 / frames;
    let reg_ok = REG_OK.load(Ordering::Relaxed);
    let reg_fail = REG_FAIL.load(Ordering::Relaxed);
    if b.stats_on {
        let hash_ms = HASH_NS.swap(0, Ordering::Relaxed) as f64 / 1e6 / frames;
        let copy_ms = UPLOAD_COPY_NS.swap(0, Ordering::Relaxed) as f64 / 1e6 / frames;
        let copy_mb = UPLOAD_BYTES.swap(0, Ordering::Relaxed) as f64 / 1e6 / frames;
        tracing::info!(
            "UFFD WP async per frame: uffd_scan_ms={scan_ms:.2} ({scans:.1} scans, {view_scans:.1} view), \
             dirty_pages={dirty_4k:.0} (4K) in dirty_regions={regions:.0}, fallbacks={fallbacks:.1}, \
             reg_ok={reg_ok} reg_fail={reg_fail}; hash_ms={hash_ms:.2}, upload_copy_ms={copy_ms:.2}, \
             upload_bytes={copy_mb:.2} MB"
        );
    } else {
        tracing::info!(
            "UFFD WP async per frame: uffd_scan_ms={scan_ms:.2} ({scans:.1} scans, {view_scans:.1} view), \
             dirty_pages={dirty_4k:.0} (4K) in dirty_regions={regions:.0}, fallbacks={fallbacks:.1}, \
             reg_ok={reg_ok} reg_fail={reg_fail}"
        );
    }
}

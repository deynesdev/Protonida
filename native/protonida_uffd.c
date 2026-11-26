#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <linux/userfaultfd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define PROTONIDA_UFFD_MAGIC 0x50524F5455464644ULL /* "PROTUFFD" */
#define PROTONIDA_UFFD_VERSION 1U
#define PAGE_SIZE_4K 4096ULL

struct UffdRange {
    uint64_t start;
    uint64_t end;
};

struct ProtonidaUffdVtable {
    uint64_t magic;
    uint32_t version;
    uint32_t ready;
    uint64_t features;
    int32_t (*uffd_register)(uint64_t addr, uint64_t len);
    int32_t (*uffd_unregister)(uint64_t addr, uint64_t len);
    int32_t (*uffd_arm)(uint64_t addr, uint64_t len);
    int64_t (*uffd_scan_rearm)(uint64_t addr, uint64_t len, struct UffdRange *out, uint32_t max_out, uint32_t check_wpasync);
};

static int g_uffd_fd = -1;
static int g_pagemap_fd = -1;
static uint64_t g_features = 0;
static int g_nohugepage = 0;

static inline uint64_t align_down_4k(uint64_t v) {
    return v & ~(PAGE_SIZE_4K - 1ULL);
}

static inline uint64_t align_up_4k(uint64_t v) {
    return (v + PAGE_SIZE_4K - 1ULL) & ~(PAGE_SIZE_4K - 1ULL);
}

static int32_t bridge_arm(uint64_t addr, uint64_t len) {
    if (g_uffd_fd < 0 || len == 0) {
        return -EINVAL;
    }
    uint64_t start = align_down_4k(addr);
    uint64_t end = align_up_4k(addr + len);
    if (end <= start) {
        return -EINVAL;
    }
    struct uffdio_writeprotect wp;
    memset(&wp, 0, sizeof(wp));
    wp.range.start = start;
    wp.range.len = end - start;
    wp.mode = UFFDIO_WRITEPROTECT_MODE_WP;
    while (ioctl(g_uffd_fd, UFFDIO_WRITEPROTECT, &wp) < 0) {
        if (errno == EINTR) {
            continue;
        }
        return -errno;
    }
    return 0;
}

static int32_t bridge_register(uint64_t addr, uint64_t len) {
    if (g_uffd_fd < 0 || len == 0) {
        return -EINVAL;
    }
    uint64_t start = align_down_4k(addr);
    uint64_t end = align_up_4k(addr + len);
    if (end <= start) {
        return -EINVAL;
    }
    if (g_nohugepage) {
        /* Disable THP on tracked range to prevent 2 MiB dirty-page amplification */
        (void)madvise((void *)(uintptr_t)start, (size_t)(end - start), MADV_NOHUGEPAGE);
    }
    struct uffdio_register reg;
    memset(&reg, 0, sizeof(reg));
    reg.range.start = start;
    reg.range.len = end - start;
    reg.mode = UFFDIO_REGISTER_MODE_WP;
    while (ioctl(g_uffd_fd, UFFDIO_REGISTER, &reg) < 0) {
        if (errno == EINTR) {
            continue;
        }
        return -errno;
    }
    return bridge_arm(start, end - start);
}

static int32_t bridge_unregister(uint64_t addr, uint64_t len) {
    if (g_uffd_fd < 0 || len == 0) {
        return -EINVAL;
    }
    uint64_t start = align_down_4k(addr);
    uint64_t end = align_up_4k(addr + len);
    if (end <= start) {
        return -EINVAL;
    }
    struct uffdio_range rng;
    rng.start = start;
    rng.len = end - start;
    while (ioctl(g_uffd_fd, UFFDIO_UNREGISTER, &rng) < 0) {
        if (errno == EINTR) {
            continue;
        }
        return -errno;
    }
    return 0;
}

static int64_t bridge_scan_rearm(
    uint64_t addr,
    uint64_t len,
    struct UffdRange *out,
    uint32_t max_out,
    uint32_t check_wpasync
) {
    if (g_pagemap_fd < 0 || len == 0 || (max_out > 0 && out == NULL)) {
        return -EINVAL;
    }
    uint64_t cur = align_down_4k(addr);
    uint64_t end = align_up_4k(addr + len);
    if (end <= cur) {
        return 0;
    }

    struct page_region scratch[512];
    int64_t total = 0;

    while (cur < end) {
        uint32_t chunk = (uint32_t)(sizeof(scratch) / sizeof(scratch[0]));
        if (max_out > 0) {
            uint32_t rem = max_out - (uint32_t)total;
            if (rem == 0) {
                /* Caller's output buffer is full; indicate truncation via -ENOSPC */
                return -ENOSPC;
            }
            if (chunk > rem) {
                chunk = rem;
            }
        }

        struct pm_scan_arg arg;
        memset(&arg, 0, sizeof(arg));
        arg.size = sizeof(arg);
        arg.flags = PM_SCAN_WP_MATCHING | (check_wpasync ? PM_SCAN_CHECK_WPASYNC : 0ULL);
        arg.start = cur;
        arg.end = end;
        arg.vec = (uint64_t)(uintptr_t)scratch;
        arg.vec_len = chunk;
        arg.max_pages = 0;
        arg.category_inverted = 0;
        arg.category_mask = PAGE_IS_WRITTEN;
        arg.category_anyof_mask = 0;
        arg.return_mask = PAGE_IS_WRITTEN;

        long ret;
        do {
            ret = ioctl(g_pagemap_fd, PAGEMAP_SCAN, &arg);
        } while (ret < 0 && errno == EINTR);

        if (ret < 0) {
            return -errno;
        }

        for (long i = 0; i < ret; i++) {
            if (out != NULL && (uint32_t)total < max_out) {
                out[total].start = scratch[i].start;
                out[total].end = scratch[i].end;
            }
            total++;
        }

        if (arg.walk_end <= cur || arg.walk_end >= end) {
            break;
        }
        cur = arg.walk_end;
    }

    return total;
}

static struct ProtonidaUffdVtable g_vtable = {
    .magic = PROTONIDA_UFFD_MAGIC,
    .version = PROTONIDA_UFFD_VERSION,
    .ready = 0,
    .features = 0,
    .uffd_register = bridge_register,
    .uffd_unregister = bridge_unregister,
    .uffd_arm = bridge_arm,
    .uffd_scan_rearm = bridge_scan_rearm,
};

__attribute__((constructor))
static void protonida_uffd_init(void) {
    const char *nhp = getenv("VICTORY_UFFD_NOHUGEPAGE");
    g_nohugepage = (nhp != NULL && nhp[0] == '1') ? 1 : 0;

    /* Step 1: Explicit UFFDIO_API(features=0) capability probe */
    int probe_fd = (int)syscall(__NR_userfaultfd, O_CLOEXEC | O_NONBLOCK | UFFD_USER_MODE_ONLY);
    if (probe_fd < 0) {
        return;
    }
    struct uffdio_api probe;
    memset(&probe, 0, sizeof(probe));
    probe.api = UFFD_API;
    probe.features = 0;
    if (ioctl(probe_fd, UFFDIO_API, &probe) < 0) {
        close(probe_fd);
        return;
    }
    uint64_t supported = probe.features;
    close(probe_fd);

    if ((supported & UFFD_FEATURE_WP_ASYNC) == 0) {
        return;
    }

    /* Step 2: Open real uffd and enable supported subset of desired features */
    int fd = (int)syscall(__NR_userfaultfd, O_CLOEXEC | O_NONBLOCK | UFFD_USER_MODE_ONLY);
    if (fd < 0) {
        return;
    }
    uint64_t desired = UFFD_FEATURE_WP_ASYNC
                     | UFFD_FEATURE_WP_UNPOPULATED
                     | UFFD_FEATURE_WP_HUGETLBFS_SHMEM;
    struct uffdio_api api;
    memset(&api, 0, sizeof(api));
    api.api = UFFD_API;
    api.features = desired & supported;
    if (ioctl(fd, UFFDIO_API, &api) < 0) {
        close(fd);
        return;
    }
    if ((api.features & UFFD_FEATURE_WP_ASYNC) == 0) {
        close(fd);
        return;
    }

    int pm = open("/proc/self/pagemap", O_RDONLY | O_CLOEXEC);
    if (pm < 0) {
        close(fd);
        return;
    }

    g_uffd_fd = fd;
    g_pagemap_fd = pm;
    g_features = api.features;
    g_vtable.features = api.features;
    g_vtable.ready = 1;

    char buf[32];
    snprintf(buf, sizeof(buf), "0x%llx", (unsigned long long)(uintptr_t)&g_vtable);
    setenv("PROTONIDA_UFFD_VTABLE", buf, 1);
}

__attribute__((destructor))
static void protonida_uffd_fini(void) {
    if (g_pagemap_fd >= 0) {
        close(g_pagemap_fd);
        g_pagemap_fd = -1;
    }
    if (g_uffd_fd >= 0) {
        close(g_uffd_fd);
        g_uffd_fd = -1;
    }
    g_vtable.ready = 0;
}

const struct ProtonidaUffdVtable *protonida_uffd_get_vtable(void) {
    return &g_vtable;
}

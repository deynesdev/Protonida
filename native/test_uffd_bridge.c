#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define PROTONIDA_UFFD_MAGIC 0x50524F5455464644ULL
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

extern const struct ProtonidaUffdVtable *protonida_uffd_get_vtable(void);

static void test_anon_private(const struct ProtonidaUffdVtable *vt) {
    const size_t npages = 32;
    const size_t len = npages * PAGE_SIZE_4K;
    uint8_t *buf = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(buf != MAP_FAILED);

    /* Register + arm unpopulated anonymous mapping */
    int32_t rc = vt->uffd_register((uint64_t)(uintptr_t)buf, len);
    assert(rc == 0);

    struct UffdRange out[64];
    /* Initial scan must report 0 dirty pages */
    int64_t n = vt->uffd_scan_rearm((uint64_t)(uintptr_t)buf, len, out, 64, 1);
    assert(n == 0);

    /* Write pages 0, 5, 6, 19 */
    buf[0 * PAGE_SIZE_4K] = 0xAA;
    buf[5 * PAGE_SIZE_4K + 123] = 0xBB;
    buf[6 * PAGE_SIZE_4K + 4095] = 0xCC;
    buf[19 * PAGE_SIZE_4K] = 0xDD;

    n = vt->uffd_scan_rearm((uint64_t)(uintptr_t)buf, len, out, 64, 1);
    assert(n == 3);
    uint64_t base = (uint64_t)(uintptr_t)buf;
    assert(out[0].start == base + 0 * PAGE_SIZE_4K && out[0].end == base + 1 * PAGE_SIZE_4K);
    assert(out[1].start == base + 5 * PAGE_SIZE_4K && out[1].end == base + 7 * PAGE_SIZE_4K);
    assert(out[2].start == base + 19 * PAGE_SIZE_4K && out[2].end == base + 20 * PAGE_SIZE_4K);

    /* Immediate rescan must report 0 dirty pages (PM_SCAN_WP_MATCHING re-armed) */
    n = vt->uffd_scan_rearm((uint64_t)(uintptr_t)buf, len, out, 64, 1);
    assert(n == 0);

    /* Re-write page 5 -> must be caught again */
    buf[5 * PAGE_SIZE_4K] = 0xEE;
    n = vt->uffd_scan_rearm((uint64_t)(uintptr_t)buf, len, out, 64, 1);
    assert(n == 1);
    assert(out[0].start == base + 5 * PAGE_SIZE_4K && out[0].end == base + 6 * PAGE_SIZE_4K);

    rc = vt->uffd_unregister((uint64_t)(uintptr_t)buf, len);
    assert(rc == 0);
    munmap(buf, len);
    printf("[PASS] MAP_PRIVATE | MAP_ANONYMOUS (unpopulated + coalescing + atomic re-arm)\n");
}

static void test_memfd_shared_aliased(const struct ProtonidaUffdVtable *vt) {
    const size_t npages = 16;
    const size_t len = npages * PAGE_SIZE_4K;
    int fd = memfd_create("protonida_test", MFD_CLOEXEC);
    assert(fd >= 0);
    assert(ftruncate(fd, (off_t)len) == 0);

    uint8_t *v1 = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    uint8_t *v2 = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(v1 != MAP_FAILED && v2 != MAP_FAILED);

    assert(vt->uffd_register((uint64_t)(uintptr_t)v1, len) == 0);
    assert(vt->uffd_register((uint64_t)(uintptr_t)v2, len) == 0);

    struct UffdRange out[32];
    assert(vt->uffd_scan_rearm((uint64_t)(uintptr_t)v1, len, out, 32, 1) == 0);
    assert(vt->uffd_scan_rearm((uint64_t)(uintptr_t)v2, len, out, 32, 1) == 0);

    /* Write through v1 -> v1 sees dirty, v2 reads updated byte */
    v1[3 * PAGE_SIZE_4K] = 0x42;
    assert(v2[3 * PAGE_SIZE_4K] == 0x42);

    int64_t n1 = vt->uffd_scan_rearm((uint64_t)(uintptr_t)v1, len, out, 32, 1);
    assert(n1 == 1);
    assert(out[0].start == (uint64_t)(uintptr_t)v1 + 3 * PAGE_SIZE_4K);
    assert(out[0].end == (uint64_t)(uintptr_t)v1 + 4 * PAGE_SIZE_4K);

    /* Immediate rescan on v1 must be 0 */
    assert(vt->uffd_scan_rearm((uint64_t)(uintptr_t)v1, len, out, 32, 1) == 0);

    /* Write through v2 -> v2 sees dirty */
    v2[10 * PAGE_SIZE_4K] = 0x99;
    assert(v1[10 * PAGE_SIZE_4K] == 0x99);
    int64_t n2 = vt->uffd_scan_rearm((uint64_t)(uintptr_t)v2, len, out, 32, 1);
    assert(n2 == 1);
    assert(out[0].start == (uint64_t)(uintptr_t)v2 + 10 * PAGE_SIZE_4K);
    assert(out[0].end == (uint64_t)(uintptr_t)v2 + 11 * PAGE_SIZE_4K);
    assert(vt->uffd_scan_rearm((uint64_t)(uintptr_t)v2, len, out, 32, 1) == 0);

    assert(vt->uffd_unregister((uint64_t)(uintptr_t)v1, len) == 0);
    assert(vt->uffd_unregister((uint64_t)(uintptr_t)v2, len) == 0);
    munmap(v1, len);
    munmap(v2, len);
    close(fd);
    printf("[PASS] memfd_create MAP_SHARED aliased views (V1 + V2 + atomic re-arm)\n");
}

int main(void) {
    const char *env = getenv("PROTONIDA_UFFD_VTABLE");
    assert(env != NULL);
    unsigned long long parsed = strtoull(env, NULL, 16);
    const struct ProtonidaUffdVtable *vt = (const struct ProtonidaUffdVtable *)(uintptr_t)parsed;
    assert(vt == protonida_uffd_get_vtable());
    assert(vt->magic == PROTONIDA_UFFD_MAGIC);
    assert(vt->ready == 1);
    printf("[INFO] PROTONIDA_UFFD_VTABLE=%s features=0x%llx\n", env, (unsigned long long)vt->features);

    test_anon_private(vt);
    test_memfd_shared_aliased(vt);
    printf("[PASS] All UFFD WP async + PAGEMAP_SCAN tests passed!\n");
    return 0;
}

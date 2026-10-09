/* x86_64 host runtime: runs the original i386 game code in IA-32e compatibility mode under Rosetta 2.
 *
 * Guest code executes natively (translated by Rosetta). Calls to imported system functions land on 12-byte
 * stubs (mov eax,idx ; ljmp 0x2b:tramp), which switch to 64-bit mode, save the guest registers into a per-thread
 * Ctx and call hle_dispatch(): either a C native or a Python callback.
 *
 * Guest stacks are 8 MB aligned regions; the Ctx lives at the top of the region so the trampoline finds it from
 * the guest ESP alone.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <pthread.h>
#include <i386/user_ldt.h>
#include <signal.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <sys/ucontext.h>
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/gl.h>

#define TRAMP_BASE   0x0F000000u      /* below 2 GB so absolute disp32 addressing works */
#define STUB_BASE    0xF0000000u
#define STACK_BASE   0xA0000000u
#define STACK_SLOT   0x800000u        /* 8 MB */
#define STACK_SLOTS  64
#define CTX_SIZE     0x400u
#define RING_BASE    0xC0000000u
#define RING_SIZE    0x400000u

struct Ctx; static void host_park(struct Ctx *);
typedef struct Ctx {
    uint64_t host_rsp;      /* 0x00 */
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi;   /* 0x08 .. 0x24 */
    uint32_t eip;           /* 0x28 */
    uint16_t sel; uint16_t pad0;   /* 0x2C */
    double   fpret;         /* 0x30 */
    uint32_t flags;         /* 0x38  bit0: push fpret on x87 stack */
    uint32_t idx;           /* 0x3C */
    uint32_t extra_pop;     /* 0x40 */
    uint32_t jump_to;       /* 0x44 */
    uint32_t stack_top;     /* 0x48 */
    uint32_t depth;         /* 0x4C */
    uint32_t tid;           /* 0x50 */
    uint32_t want_fp;       /* 0x54: caller expects a float/double result in st0 */
    volatile uint32_t state;  /* 0x58: 0 guest running, 1 request pending (guest->host), 2 response ready (host->guest) */
    uint32_t kind;          /* 0x5C: resume kind: 0 ret, 1 ret 4, 2 jump to eip */
    uint32_t started;       /* 0x60: native guest thread exists */
    volatile uint32_t parked; /* 0x64 */
    uint32_t park_eip;      /* 0x68: m16:32 far pointer used by park64 to re-enter the wait loop */
    uint16_t park_sel;      /* 0x6C */
    uint32_t ring_base;     /* 0x70: async GL command ring of this thread (1 MB) */
    uint32_t pad1[3];
    volatile uint32_t rhead; /* 0x80 (producer = guest) */
    uint32_t pad2[15];
    volatile uint32_t rtail; /* 0xC0 (consumer = servicer) */
    uint32_t pad3[15];
    uint32_t mach_thr;      /* 0x100: mach port of the native guest thread (suspended while it is idle) */
    uint32_t suspended;
} Ctx;
#include "gtramp_blob.h"
#define CALL_DONE 0xFFFFFFu

uint32_t g_ret_stub;

typedef void (*native_fn)(Ctx *);
typedef void (*py_cb_t)(Ctx *);

static native_fn *natives;          /* indexed by hook idx */
static int n_natives;
static py_cb_t py_cb;
static py_cb_t ring_cb;
void host_set_ring_callback(py_cb_t cb) { ring_cb = cb; }
static uint16_t sel_cs32, sel_ds32;
static uint8_t *tramp;              /* low-memory code */
static pthread_mutex_t stack_lock = PTHREAD_MUTEX_INITIALIZER;
/* Guest-execution lock: held exactly while a thread executes guest (compat-mode) code, so mode switches of different
 * threads never overlap (Rosetta mis-tracks the CPU mode when several threads switch concurrently). */
void host_set_ggl(int on) { (void)on; }
static int stack_used[STACK_SLOTS];
static int stack_mapped[STACK_SLOTS];
static __thread Ctx *tls_ctx;

/* offsets inside the trampoline region (64-bit code, data and 32-bit code live in separate pages) */
#define OFF_DATA      0x1000        /* [0]=cs32, [8]... see gtramp.s: cs32@0 ds32@8 park_fn@16 */
#define OFF_ENTER32   0x100
#define OFF_PARK64    0x300
#define OFF_TRAMP     0x2000        /* tramp32: hook request */
#define OFF_DONE32    0x2200
#define OFF_PAD_ENTRY 0x2300        /* call eax ; jmp done32 */

extern uint32_t hle_dispatch_c(Ctx *);

/* ----------------------------------------------------------------------------------- code generation */
static size_t emit(uint8_t *p, const char *hex) {
    size_t n = 0;
    for (; hex[0]; ) {
        if (hex[0] == ' ') { hex++; continue; }
        unsigned v; sscanf(hex, "%2x", &v); p[n++] = (uint8_t)v; hex += 2;
    }
    return n;
}

static void put32(uint8_t *p, size_t at, uint32_t v) { memcpy(p + at, &v, 4); }

static void build_pads(void) {
    memcpy(tramp + OFF_PARK64, blob_park64, sizeof blob_park64);
    memcpy(tramp + OFF_TRAMP, blob_guest32, sizeof blob_guest32);
}

/* Hand-assembled machine code (x86-64). Register use: r10 = Ctx*. */
static void build_trampolines(void) {
    uint8_t *t = tramp;
    uint32_t data = TRAMP_BASE + OFF_DATA;
    size_t n;

    /* ---------------- enter32(Ctx *rdi, uint32 eip esi, uint32 esp edx) ---------------- */
    uint8_t *p = t + OFF_ENTER32; n = 0;
    n += emit(p + n, "53 55 41 54 41 55 41 56 41 57");           /* push rbx,rbp,r12..r15 */
    n += emit(p + n, "48 8B 07 50");                              /* mov rax,[rdi]; push rax */
    n += emit(p + n, "48 89 27");                                 /* mov [rdi],rsp */
    n += emit(p + n, "49 89 FA");                                 /* mov r10,rdi */
    n += emit(p + n, "41 89 72 28");                              /* mov [r10+0x28],esi */
    n += emit(p + n, "66 8B 04 25"); put32(p, n, data + 8); n += 4;          /* mov ax,[cs32 slot] */
    n += emit(p + n, "66 41 89 42 2C");                           /* mov [r10+0x2c],ax */
    n += emit(p + n, "66 8B 04 25"); put32(p, n, data + 10); n += 4;         /* mov ax,[ds32 slot] */
    n += emit(p + n, "8E D8 8E C0 8E D0");                        /* mov ds,ax ; mov es,ax ; mov ss,ax */
    n += emit(p + n, "89 D4");                                    /* mov esp,edx */
    n += emit(p + n, "89 C8");                                    /* mov eax,ecx  (guest function address) */
    n += emit(p + n, "31 C9 31 D2 31 DB 31 ED 31 F6 31 FF");      /* xor ecx,edx,ebx,ebp,esi,edi */
    n += emit(p + n, "41 FF 6A 28");                              /* ljmp *0x28(%r10) (m16:32) */
}

/* ----------------------------------------------------------------------------------- dispatch */
static volatile uint32_t ring[256]; static volatile uint32_t ring_pos;
uint32_t hle_dispatch_c(Ctx *ctx) {
    uint32_t idx = ctx->idx;
    {   uint32_t ra = *(uint32_t *)(uintptr_t)ctx->esp;
        if (!((ra >= 0x2000 && ra < 0x705000) || (ra >= 0xE0000000u && ra < 0xE0100000u) || ra == g_ret_stub || ra == TRAMP_BASE + OFF_PAD_ENTRY + 2)) {
            char b[400]; int k = snprintf(b, sizeof b, "[corrupt] hook idx %u (tid %u) entered with bad return address %x esp %x; stack:", idx, ctx->tid, ra, ctx->esp);
            write(2, b, k);
            for (int i = -2; i < 16; i++) { k = snprintf(b, sizeof b, " %x", *(uint32_t *)(uintptr_t)(ctx->esp + i * 4)); write(2, b, k); }
            write(2, "\n", 1);
            raise(SIGSEGV);
        } }
    ring[__sync_fetch_and_add(&ring_pos, 1) & 255] = idx | (ctx->tid << 24);
    ctx->flags = 0; ctx->extra_pop = 0; ctx->jump_to = 0;
    if (idx < (uint32_t)n_natives && natives[idx]) natives[idx](ctx);
    else if (py_cb) py_cb(ctx);
    uint32_t esp = ctx->esp;
    if (ctx->jump_to) {
        ctx->eip = ctx->jump_to; ctx->kind = 2;  /* tail call into guest code; its own `ret` returns to the caller */
    } else if (ctx->extra_pop == 0) {
        ctx->kind = 0;
    } else if (ctx->extra_pop == 4) {
        ctx->kind = 1;
    } else {
        ctx->eip = *(uint32_t *)(uintptr_t)esp; ctx->kind = 2;
        ctx->esp = esp + 4 + ctx->extra_pop;
    }
    return 0;
}

/* ----------------------------------------------------------------------------------- API */
typedef uint64_t (*enter32_fn)(Ctx *, uint32_t, uint32_t, uint32_t);

int host_init(void) {
    tramp = mmap((void *)(uintptr_t)TRAMP_BASE, 0x10000, PROT_READ | PROT_WRITE | PROT_EXEC,
                 MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
    if (tramp == MAP_FAILED) return -1;
    uint64_t cs_desc = 0x00CFFA000000FFFFULL, ds_desc = 0x00CFF2000000FFFFULL;
    int cs_idx = -1, ds_idx = -1;
    for (int st = 1; st < 64 && cs_idx < 0; st++)
        if (i386_set_ldt(st, (union ldt_entry *)&cs_desc, 1) >= 0) cs_idx = st;
    for (int st = cs_idx + 1; st < 64 && ds_idx < 0; st++)
        if (i386_set_ldt(st, (union ldt_entry *)&ds_desc, 1) >= 0) ds_idx = st;
    if (cs_idx < 0 || ds_idx < 0) return -2;
    sel_cs32 = (uint16_t)((cs_idx << 3) | 7);
    sel_ds32 = (uint16_t)((ds_idx << 3) | 7);
    *(uint64_t *)(tramp + OFF_DATA + 16) = (uint64_t)(uintptr_t)host_park;
    *(uint16_t *)(tramp + OFF_DATA + 8) = sel_cs32;
    *(uint16_t *)(tramp + OFF_DATA + 10) = sel_ds32;
    build_trampolines();
    build_pads();
    return 0;
}

uint32_t host_tramp_addr(void) { return TRAMP_BASE + OFF_TRAMP; }
uint32_t host_cs64(void) { return 0x2B; }

void host_set_py_callback(py_cb_t cb) { py_cb = cb; }
void host_set_natives(native_fn *tab, int n) { natives = tab; n_natives = n; }
void host_set_native(int idx, native_fn fn) {
    if (idx >= n_natives) {
        int nn = idx + 1024;
        natives = realloc(natives, nn * sizeof(native_fn));
        memset(natives + n_natives, 0, (nn - n_natives) * sizeof(native_fn));
        n_natives = nn;
    }
    natives[idx] = fn;
}

/* Allocate a guest stack slot + Ctx for the calling host thread (the "servicer"). The guest code of that thread runs
 * on its own native thread in compat mode; it hands every hook request to the servicer through ctx->state. */
static pthread_mutex_t pm = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pc = PTHREAD_COND_INITIALIZER;

Ctx *host_thread_init(void) {
    if (tls_ctx) return tls_ctx;
    pthread_mutex_lock(&stack_lock);
    int slot = -1;
    for (int i = 0; i < STACK_SLOTS; i++) if (!stack_used[i]) { slot = i; stack_used[i] = 1; break; }
    pthread_mutex_unlock(&stack_lock);
    if (slot < 0) return NULL;
    uint32_t base = STACK_BASE + (uint32_t)slot * STACK_SLOT;
    if (!stack_mapped[slot]) {
        void *m = mmap((void *)(uintptr_t)base, STACK_SLOT, PROT_READ | PROT_WRITE | PROT_EXEC,
                       MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
        if (m == MAP_FAILED) return NULL;
        void *rm = mmap((void *)(uintptr_t)(RING_BASE + (uint32_t)slot * RING_SIZE), RING_SIZE, PROT_READ | PROT_WRITE,
                        MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
        if (rm == MAP_FAILED) return NULL;
        stack_mapped[slot] = 1;
    }
    Ctx *ctx = (Ctx *)(uintptr_t)(base + STACK_SLOT - CTX_SIZE);
    if (!ctx->started) {
        memset(ctx, 0, CTX_SIZE);
        ctx->stack_top = base + STACK_SLOT - CTX_SIZE - 64;
        ctx->tid = (uint32_t)slot + 1;
        ctx->park_eip = TRAMP_BASE + 0x2100; ctx->park_sel = sel_cs32;
        ctx->ring_base = RING_BASE + (uint32_t)slot * RING_SIZE;
    } else { ctx->depth = 0; }
    tls_ctx = ctx;
    return ctx;
}

void host_thread_release(void) {
    if (!tls_ctx) return;
    uint32_t base = (uint32_t)((uintptr_t)tls_ctx & ~(uintptr_t)(STACK_SLOT - 1));
    int slot = (base - STACK_BASE) / STACK_SLOT;
    /* the slot (stack, ctx and its parked guest thread) is kept and reused by the next host thread */
    pthread_mutex_lock(&stack_lock); stack_used[slot] = 0; pthread_mutex_unlock(&stack_lock);
    tls_ctx = NULL;
}

Ctx *host_cur_ctx(void) { return tls_ctx; }
uint32_t host_ctx_tid(Ctx *c) { return c->tid; }

/* ---- native replay of the async GL command ring (runs on the servicer thread, where the GL context is current) ---- */
#define RING_WORDS 0x100000u
static uint8_t *replay_op; static int n_replay;
void host_set_replay(int idx, int op) {
    if (idx >= n_replay) {
        int nn = idx + 1024; replay_op = realloc(replay_op, nn); memset(replay_op + n_replay, 0, nn - n_replay); n_replay = nn;
    }
    replay_op[idx] = (uint8_t)op;
}
static uint32_t rep_array_buf, rep_fbo, big_fbo;
static float rt_scale = 1.0f;
static GLint last_vp[4], last_sc[4];
void host_set_big_fbo(uint32_t id) { big_fbo = id; }
void host_set_rt_scale(float f) { rt_scale = f; }
uint32_t host_get_rep_fbo(void) { return rep_fbo; }
static inline int on_big(void) { return big_fbo && rep_fbo == big_fbo; }
#define SC(v) ((GLint)((float)(GLint)(v) * rt_scale + 0.5f))
static inline float fw(uint32_t w) { float f; memcpy(&f, &w, 4); return f; }
static void do_replay(int op, const uint32_t *a) {
    switch (op) {
    case 1: glUniform1i((GLint)a[0], (GLint)a[1]); break;
    case 2: glUniform1f((GLint)a[0], fw(a[1])); break;
    case 3: glUniform2f((GLint)a[0], fw(a[1]), fw(a[2])); break;
    case 4: glUniform3f((GLint)a[0], fw(a[1]), fw(a[2]), fw(a[3])); break;
    case 5: glUniform4f((GLint)a[0], fw(a[1]), fw(a[2]), fw(a[3]), fw(a[4])); break;
    case 6: glUniform1fv((GLint)a[0], (GLsizei)a[1], (const GLfloat *)(uintptr_t)a[2]); break;
    case 7: glUniform2fv((GLint)a[0], (GLsizei)a[1], (const GLfloat *)(uintptr_t)a[2]); break;
    case 8: glUniform3fv((GLint)a[0], (GLsizei)a[1], (const GLfloat *)(uintptr_t)a[2]); break;
    case 9: glUniform4fv((GLint)a[0], (GLsizei)a[1], (const GLfloat *)(uintptr_t)a[2]); break;
    case 10: glUniform1iv((GLint)a[0], (GLsizei)a[1], (const GLint *)(uintptr_t)a[2]); break;
    case 11: glUniform2iv((GLint)a[0], (GLsizei)a[1], (const GLint *)(uintptr_t)a[2]); break;
    case 12: glUniform3iv((GLint)a[0], (GLsizei)a[1], (const GLint *)(uintptr_t)a[2]); break;
    case 13: glUniform4iv((GLint)a[0], (GLsizei)a[1], (const GLint *)(uintptr_t)a[2]); break;
    case 14: glUniformMatrix2fv((GLint)a[0], (GLsizei)a[1], a[2] & 0xFF, (const GLfloat *)(uintptr_t)a[3]); break;
    case 15: glUniformMatrix3fv((GLint)a[0], (GLsizei)a[1], a[2] & 0xFF, (const GLfloat *)(uintptr_t)a[3]); break;
    case 16: glUniformMatrix4fv((GLint)a[0], (GLsizei)a[1], a[2] & 0xFF, (const GLfloat *)(uintptr_t)a[3]); break;
    case 17: glVertexAttrib1f(a[0], fw(a[1])); break;
    case 18: glVertexAttrib2f(a[0], fw(a[1]), fw(a[2])); break;
    case 19: glVertexAttrib3f(a[0], fw(a[1]), fw(a[2]), fw(a[3])); break;
    case 20: glVertexAttrib4f(a[0], fw(a[1]), fw(a[2]), fw(a[3]), fw(a[4])); break;
    case 21: if (rep_array_buf) glVertexAttribPointer(a[0], (GLint)a[1], a[2], a[3] & 0xFF, (GLsizei)a[4], (const void *)(uintptr_t)a[5]); break;
    case 22: glEnableVertexAttribArray(a[0]); break;
    case 23: glDisableVertexAttribArray(a[0]); break;
    case 24: if (a[0] == GL_ARRAY_BUFFER) rep_array_buf = a[1]; glBindBuffer(a[0], a[1]); break;
    case 25: glBlendFunc(a[0], a[1]); break;
    case 26: glBlendFuncSeparate(a[0], a[1], a[2], a[3]); break;
    case 27: glBlendEquation(a[0]); break;
    case 28: glDepthMask(a[0] & 0xFF); break;
    case 29: glDepthFunc(a[0]); break;
    case 30: glCullFace(a[0]); break;
    case 31: glFrontFace(a[0]); break;
    case 32: glEnable(a[0]); break;
    case 33: glDisable(a[0]); break;
    case 34: for (int i = 0; i < 4; i++) last_vp[i] = (GLint)a[i];
             if (on_big()) glViewport(SC(a[0]), SC(a[1]), SC(a[2]), SC(a[3])); else glViewport((GLint)a[0], (GLint)a[1], (GLsizei)a[2], (GLsizei)a[3]); break;
    case 35: for (int i = 0; i < 4; i++) last_sc[i] = (GLint)a[i];
             if (on_big()) glScissor(SC(a[0]), SC(a[1]), SC(a[2]), SC(a[3])); else glScissor((GLint)a[0], (GLint)a[1], (GLsizei)a[2], (GLsizei)a[3]); break;
    case 45: glBegin(a[0]); break;                 /* immediate mode (UI effects such as the button ripples): one command per vertex */
    case 46: glEnd(); break;
    case 47: glVertex2f(fw(a[0]), fw(a[1])); break;
    case 48: glTexCoord2f(fw(a[0]), fw(a[1])); break;
    case 49: glColor4f(fw(a[0]), fw(a[1]), fw(a[2]), fw(a[3])); break;
    case 44: {   /* the game sets the viewport *before* binding its scene target: re-apply it scaled (and raw again when leaving) */
        int was = on_big();
        rep_fbo = a[1]; glBindFramebufferEXT(a[0], a[1]);
        if (on_big()) { glViewport(SC(last_vp[0]), SC(last_vp[1]), SC(last_vp[2]), SC(last_vp[3])); glScissor(SC(last_sc[0]), SC(last_sc[1]), SC(last_sc[2]), SC(last_sc[3])); }
        else if (was) { glViewport(last_vp[0], last_vp[1], last_vp[2], last_vp[3]); glScissor(last_sc[0], last_sc[1], last_sc[2], last_sc[3]); }
        break; }
    case 36: glClear(a[0]); break;
    case 37: glClearColor(fw(a[0]), fw(a[1]), fw(a[2]), fw(a[3])); break;
    case 38: glColorMask(a[0] & 0xFF, a[1] & 0xFF, a[2] & 0xFF, a[3] & 0xFF); break;
    case 39: glPolygonOffset(fw(a[0]), fw(a[1])); break;
    case 40: glLineWidth(fw(a[0])); break;
    case 41: glDrawElements(a[0], (GLsizei)a[1], a[2], (const void *)(uintptr_t)a[3]); break;
    case 42: glDrawArrays(a[0], (GLint)a[1], (GLsizei)a[2]); break;
    case 43: {   /* draw with client arrays snapshotted into the ring: [kind, mode, a, count, typ, idxptr, nattr, nattr * 6, ...] */
        uint32_t nat = a[6];
        for (uint32_t k = 0; k < nat; k++) {
            const uint32_t *e = a + 7 + 6 * k;
            glVertexAttribPointer(e[0], (GLint)e[1], e[2], e[3] & 0xFF, (GLsizei)e[4], (const void *)(uintptr_t)e[5]);
        }
        if (a[0]) glDrawElements(a[1], (GLsizei)a[3], a[4], (const void *)(uintptr_t)a[5]);
        else glDrawArrays(a[1], (GLint)a[2], (GLsizei)a[3]);
        break; }
    }
}

/* replay everything queued so far; entries without a native implementation go to the python callback one at a time */
static void drain_ring(Ctx *c) {
    const uint32_t *ring = (const uint32_t *)(uintptr_t)c->ring_base;
    uint32_t tail = c->rtail, head = c->rhead;
    while (tail != head) {
        uint32_t off = tail & (RING_WORDS - 1);
        if (RING_WORDS - off < 2) { tail += RING_WORDS - off; c->rtail = tail; continue; }
        uint32_t idx = ring[off], need = ring[off + 1];
        if (idx == 0xFFFFFFFFu) { tail += need; c->rtail = tail; continue; }
        if (idx < (uint32_t)n_replay && replay_op[idx]) { do_replay(replay_op[idx], ring + off + 2); tail += need; c->rtail = tail; }
        else { c->rtail = tail; ring_cb(c); tail = c->rtail; }          /* python consumes exactly one entry */
        head = c->rhead;
    }
}

/* python-side condition variables re-acquire the guest mutex (owner word at +4) through this */
void host_mutex_lock(uint32_t addr, uint32_t me) {
    volatile uint32_t *o = (volatile uint32_t *)(uintptr_t)(addr + 4);
    while (!__sync_bool_compare_and_swap(o, 0, me)) sched_yield();
}

/* a guest thread whose servicer blocks (cond wait, sleep, join) must not spin meanwhile: stop it for the duration */
void host_idle(Ctx *c, int on) {
    if (!c || !c->mach_thr) return;
    if (on) { if (!c->suspended && thread_suspend(c->mach_thr) == KERN_SUCCESS) c->suspended = 1; }
    else if (c->suspended) { thread_resume(c->mach_thr); c->suspended = 0; }
}

/* servicer side: wait until ctx->state == want, spinning first and sleeping when the other side is slow */
static int spin_limit = -1;
static void wait_state(Ctx *c, uint32_t want) {
    int n = 0;
    if (spin_limit < 0) { const char *v = getenv("A6_SPINN"); spin_limit = v ? atoi(v) : 400000; }
    for (;;) {
        if (c->rhead != c->rtail && ring_cb) { drain_ring(c); n = 0; continue; }       /* replay queued async GL calls */
        if (c->state == want) {
            if (c->rhead != c->rtail && ring_cb) { drain_ring(c); continue; }           /* queued before the request */
            return;
        }
        if (n < spin_limit) { __builtin_ia32_pause(); n++; }
        else if (n < spin_limit + 2000) { usleep(20); n++; }
        else usleep(200);
    }
}

/* guest side, 64-bit: a guest thread that spun for too long waits here (called from park64 on its native thread) */
static void host_park(Ctx *c) {
    if (getenv("A6_DBGSV")) { char b[100]; int k = snprintf(b, sizeof b, "[park] enter state %u\n", c->state); write(2, b, k); }
    pthread_mutex_lock(&pm);
    c->parked = 1;
    while (c->state != 2) pthread_cond_wait(&pc, &pm);
    c->parked = 0;
    pthread_mutex_unlock(&pm);
    if (getenv("A6_DBGSV")) write(2, "[park] leave\n", 13);
}

static void post_response(Ctx *c) {
    if (getenv("A6_DBGSV")) { char b[100]; int k = snprintf(b, sizeof b, "[post] kind %u eip %x esp %x eax %x\n", c->kind, c->eip, c->esp, c->eax); write(2, b, k); }
    __atomic_store_n(&c->state, 2, __ATOMIC_SEQ_CST);
    if (c->parked) { pthread_mutex_lock(&pm); pthread_cond_broadcast(&pc); pthread_mutex_unlock(&pm); }
}

typedef uint64_t (*enter32_fn)(Ctx *, uint32_t, uint32_t, uint32_t);
struct gstart { Ctx *ctx; uint32_t sp, fn; };
static void *guest_thread_main(void *arg) {
    struct gstart g = *(struct gstart *)arg; free(arg);
    tls_ctx = g.ctx;
    g.ctx->mach_thr = mach_thread_self();
    ((enter32_fn)(tramp + OFF_ENTER32))(g.ctx, TRAMP_BASE + OFF_PAD_ENTRY, g.sp, g.fn);
    return NULL;                     /* never reached: the guest thread parks forever between calls */
}

/* Call a guest cdecl function. Returns edx:eax. */
uint64_t host_guest_call_ex(uint32_t fn, int n, const uint32_t *args, int want_fp, double *fpret) {
    Ctx *ctx = tls_ctx ? tls_ctx : host_thread_init();
    uint32_t base = ctx->depth ? ctx->esp - 0x100 : ctx->stack_top;
    uint32_t sp = (base - 4u * (uint32_t)n - 64u) & ~0xFu;      /* args at sp.. ; `call eax` in the pad pushes the return address */
    for (int i = 0; i < n; i++) ((uint32_t *)(uintptr_t)sp)[i] = args[i];
    Ctx saved; memcpy(&saved, ctx, 0x58);
    ctx->depth++; ctx->want_fp = want_fp;
    if (!ctx->started) {
        ctx->started = 1; ctx->state = 0;
        struct gstart *g = malloc(sizeof *g); g->ctx = ctx; g->sp = sp; g->fn = fn;
        pthread_attr_t at; pthread_attr_init(&at); pthread_attr_setstacksize(&at, 1 << 20);
        pthread_t th; pthread_create(&th, &at, guest_thread_main, g); pthread_detach(th);
    } else {
        if (ctx->suspended) { thread_resume(ctx->mach_thr); ctx->suspended = 0; }
        ctx->eax = fn; ctx->eip = TRAMP_BASE + OFF_PAD_ENTRY; ctx->esp = sp; ctx->kind = 2; ctx->flags = 0;
        post_response(ctx);
    }
    for (;;) {
        wait_state(ctx, 1);
        if (getenv("A6_DBGSV")) { char b[100]; int k = snprintf(b, sizeof b, "[sv] tid %u idx %x esp %x\n", ctx->tid, ctx->idx, ctx->esp); write(2, b, k); }
        if (ctx->idx == CALL_DONE) break;
        hle_dispatch_c(ctx);
        post_response(ctx);
    }
    uint64_t r = ((uint64_t)ctx->edx << 32) | ctx->eax;
    ctx->depth--;
    /* idle guest thread: stop it instead of letting it spin (a plain suspend; no mode switch involved) */
    if (ctx->depth == 0 && ctx->mach_thr && !getenv("A6_NOSUSPEND")) { if (thread_suspend(ctx->mach_thr) == KERN_SUCCESS) ctx->suspended = 1; }
    if (want_fp && fpret) *fpret = ctx->fpret;
    memcpy((char *)ctx + 8, (char *)&saved + 8, 0x50);   /* not host_rsp: the guest thread's 64-bit stack pointer is live */
    return r;
}
uint64_t host_guest_call(uint32_t fn, int n, const uint32_t *args) { return host_guest_call_ex(fn, n, args, 0, NULL); }

/* Install the stub for hook idx: mov eax,idx ; jmp tramp32 (all in compat mode) */
void host_make_stub(uint32_t addr, uint32_t idx) {
    uint8_t *p = (uint8_t *)(uintptr_t)addr;
    p[0] = 0xB8; memcpy(p + 1, &idx, 4);
    p[5] = 0xE9; uint32_t rel = (TRAMP_BASE + OFF_TRAMP) - (addr + 10); memcpy(p + 6, &rel, 4);
}
/* a jump to this stub ends the outermost guest call (used to abort NSApplicationMain) */
void host_make_ret_stub(uint32_t addr) {
    uint8_t *p = (uint8_t *)(uintptr_t)addr;
    p[0] = 0xE9; uint32_t rel = (TRAMP_BASE + OFF_DONE32) - (addr + 5); memcpy(p + 1, &rel, 4);
    g_ret_stub = addr;
}

void *host_mmap_fixed(uint32_t addr, uint32_t size) {
    return mmap((void *)(uintptr_t)addr, size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
}

/* ---- crash diagnostics: report the faulting address and registers (guest registers as seen by Rosetta) ---- */
static struct sigaction old_sa[32];

/* ---- dev: read/write watchpoints via mprotect (find which guest code touches a data range) ---- */
#define NW 4
static uintptr_t w_lo[NW], w_hi[NW]; static int n_w;
static volatile int w_rearm;
#define WLOG 2048
#define WSTK 48
static uint32_t wlog[WLOG][2 + WSTK]; static volatile uint32_t wpos;
static int w_err;
static volatile int w_pending[NW];
static void w_prot_one(int i, int on) {
    uintptr_t a = w_lo[i] & ~(uintptr_t)0x3FFF, e = (w_hi[i] + 0x3FFF) & ~(uintptr_t)0x3FFF;
    if (mprotect((void *)a, e - a, on ? PROT_NONE : (PROT_READ | PROT_WRITE | PROT_EXEC))) w_err = errno;
}
static void w_protect(int on) { for (int i = 0; i < n_w; i++) w_prot_one(i, on); }
int host_watch_err(void) { return w_err; }
static void *w_thread(void *arg) {
    for (;;) {
        struct timespec ts = {0, 40000}; nanosleep(&ts, NULL);
        for (int i = 0; i < n_w; i++) if (w_pending[i]) { w_pending[i] = 0; w_prot_one(i, 1); }
    }
    return NULL;
}
void host_watch_add(uint32_t lo, uint32_t hi) {
    static int started; if (n_w < NW) { w_lo[n_w] = lo; w_hi[n_w] = hi; n_w++; }
    if (!started) { started = 1; pthread_t t; pthread_create(&t, NULL, w_thread, NULL); pthread_detach(t); }
    w_prot_one(n_w - 1, 1);
}
void host_watch_stop(void) { w_protect(0); n_w = 0; }
uint32_t host_watch_pos(void) { return wpos; }
uint32_t *host_watch_log(void) { return &wlog[0][0]; }
static int watch_fault(siginfo_t *si, ucontext_t *uc) {
    uintptr_t a = (uintptr_t)si->si_addr; int hit = -1;
    if (getenv("A6_WATCHDBG")) { char b[100]; int k = snprintf(b, sizeof b, "[wfault] %p n_w %d\n", si->si_addr, n_w); write(2, b, k); }
    for (int i = 0; i < n_w; i++) {
        uintptr_t pa = w_lo[i] & ~(uintptr_t)0x3FFF, pe = (w_hi[i] + 0x3FFF) & ~(uintptr_t)0x3FFF;
        if (a >= pa && a < pe) { hit = i; break; }
    }
    if (hit < 0) return 0;
    if (a >= w_lo[hit] && a < w_hi[hit] && wpos < WLOG) {
        uint32_t *r = wlog[wpos]; r[0] = (uint32_t)a; r[1] = (uint32_t)uc->uc_mcontext->__ss.__rsp;
        uint32_t sp = r[1]; for (int k = 16; k < WSTK; k++) r[2 + k] = *(uint32_t *)(uintptr_t)(sp + 4 * (k - 16));
        uint32_t bp = (uint32_t)uc->uc_mcontext->__ss.__rbp;
        for (int k = 0; k < 16; k++) {
            if (bp < 0x1000 || (bp & 3)) { r[2 + k] = 0; continue; }
            r[2 + k] = *(uint32_t *)(uintptr_t)(bp + 4);
            uint32_t nb = *(uint32_t *)(uintptr_t)bp; if (nb <= bp) bp = 0; else bp = nb;
        }
        wpos++;
    }
    w_prot_one(hit, 0); w_pending[hit] = 1;
    return 1;
}
static void segv_handler(int sig, siginfo_t *si, void *uctx) {
    ucontext_t *uc = (ucontext_t *)uctx;
    if (n_w && (sig == SIGSEGV || sig == SIGBUS) && watch_fault(si, uc)) return;
    char buf[512];
    int n = snprintf(buf, sizeof buf, "\n[crash] signal %d fault addr %p rip %llx rsp %llx rax %llx rcx %llx rdx %llx rbx %llx rsi %llx rdi %llx\n",
        sig, si->si_addr, (unsigned long long)uc->uc_mcontext->__ss.__rip, (unsigned long long)uc->uc_mcontext->__ss.__rsp,
        (unsigned long long)uc->uc_mcontext->__ss.__rax, (unsigned long long)uc->uc_mcontext->__ss.__rcx,
        (unsigned long long)uc->uc_mcontext->__ss.__rdx, (unsigned long long)uc->uc_mcontext->__ss.__rbx,
        (unsigned long long)uc->uc_mcontext->__ss.__rsi, (unsigned long long)uc->uc_mcontext->__ss.__rdi);
    write(2, buf, n);
    Ctx *c = tls_ctx;
    if (c) { n = snprintf(buf, sizeof buf, "[crash] tid %u ctx esp %x eip %x depth %u last idx %u\n", c->tid, c->esp, c->eip, c->depth, c->idx); write(2, buf, n); }
    if (c) { Dl_info di; uint64_t rip = uc->uc_mcontext->__ss.__rip; const char *nm = "?"; long off = 0;
        if (dladdr((void *)rip, &di) && di.dli_sname) { nm = di.dli_sname; off = (long)(rip - (uint64_t)di.dli_saddr); }
        n = snprintf(buf, sizeof buf, "[crash] ctx host_rsp %llx sel %x flags %x extra_pop %x | rip in %s+%ld cs %llx rflags %llx\n", *(unsigned long long *)c, *(uint16_t *)((char *)c + 0x2c), *(uint32_t *)((char *)c + 0x38), *(uint32_t *)((char *)c + 0x40), nm, off, (unsigned long long)uc->uc_mcontext->__ss.__cs, (unsigned long long)uc->uc_mcontext->__ss.__rflags); write(2, buf, n); }
    {   uint32_t rsp = (uint32_t)uc->uc_mcontext->__ss.__rsp;
        n = snprintf(buf, sizeof buf, "[crash] stack @%x:", rsp - 32); write(2, buf, n);
        for (int i = -8; i < 24; i++) { n = snprintf(buf, sizeof buf, " %x", *(uint32_t *)(uintptr_t)(rsp + i * 4)); write(2, buf, n); }
        write(2, "\n", 1); }
    {   uint32_t li = ring[(ring_pos - 1) & 255] & 0xFFFFFF;
        uint8_t *st = (uint8_t *)(uintptr_t)(STUB_BASE + li * 16);
        n = snprintf(buf, sizeof buf, "[crash] stub[%u]:", li); write(2, buf, n);
        for (int i = 0; i < 16; i++) { n = snprintf(buf, sizeof buf, " %02x", st[i]); write(2, buf, n); }
        n = snprintf(buf, sizeof buf, "  tramp data: %llx cs %x ds %x  tramp code:", *(unsigned long long *)(tramp + OFF_DATA), *(uint16_t *)(tramp + OFF_DATA + 8), *(uint16_t *)(tramp + OFF_DATA + 10)); write(2, buf, n);
        for (int i = 0; i < 12; i++) { n = snprintf(buf, sizeof buf, " %02x", tramp[OFF_TRAMP + i]); write(2, buf, n); }
        write(2, "\n", 1); }
    uint32_t p = ring_pos;
    n = snprintf(buf, sizeof buf, "[crash] last hooks (idx|tid<<24):"); write(2, buf, n);
    for (int i = 24; i >= 1; i--) { n = snprintf(buf, sizeof buf, " %x", ring[(p - i) & 255]); write(2, buf, n); }
    write(2, "\n", 1);
    /* chain to the previously installed handler (python's faulthandler): it prints the python traceback of every thread */
    if (sig > 0 && sig < 32 && (old_sa[sig].sa_flags & SA_SIGINFO) && old_sa[sig].sa_sigaction) {
        old_sa[sig].sa_sigaction(sig, si, uctx);
    } else if (sig > 0 && sig < 32 && old_sa[sig].sa_handler != SIG_DFL && old_sa[sig].sa_handler != SIG_IGN && old_sa[sig].sa_handler) {
        old_sa[sig].sa_handler(sig);
    }
    _exit(139);
}
void host_install_crash_handler(void) {
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = segv_handler; sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigaction(SIGSEGV, &sa, &old_sa[SIGSEGV]); sigaction(SIGBUS, &sa, &old_sa[SIGBUS]); sigaction(SIGILL, &sa, &old_sa[SIGILL]);
}

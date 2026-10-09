/* Hot libc routines compiled to i386 and executed inside the emulator (no python round trip).
 * Must stay free of globals / string literals / switch tables: only __text is loaded. */
typedef unsigned int size_t;
typedef unsigned char u8;

#define ST ((struct state *)0xE0080000)
struct state {
    unsigned heap_ptr;       /* bump pointer */
    unsigned heap_end;
    unsigned bins[64];       /* free list heads per size class */
    unsigned cur;            /* current guest thread handle */
    unsigned slow_lock;      /* trap addresses for the python slow paths */
    unsigned slow_unlock;
    unsigned lock;           /* allocator spinlock (real threads) */
    unsigned qhead, qtail, qbytes;   /* quarantine of freed blocks (see g_free) */
};
#define QRING ((unsigned *)0xE00B0000)
#define QN 16384u
#define QMAX_BYTES (8u << 20)
#define ARENA_LO 0x30000000u

static inline void *rep_movsb(void *d, const void *s, size_t n) {
    void *r = d;
    __asm__ volatile("rep movsb" : "+D"(d), "+S"(s), "+c"(n) : : "memory");
    return r;
}

/* macOS's memcpy is memmove: the game relies on that for overlapping moves (a forward byte copy corrupts them) */
void *g_memmove(void *d, const void *s, size_t n);
void *g_memcpy(void *d, const void *s, size_t n) {
    if ((size_t)((u8 *)d - (u8 *)s) - 1 < n - 1) return g_memmove(d, s, n);     /* 0 < d - s < n: overlapping, d above s */
    return rep_movsb(d, s, n);
}

void *g_memmove(void *d, const void *s, size_t n) {
    u8 *dd = (u8 *)d; const u8 *ss = (const u8 *)s;
    if (dd == ss || n == 0) return d;
    if (dd < ss || dd >= ss + n) return rep_movsb(d, s, n);
    dd += n - 1; ss += n - 1;
    __asm__ volatile("std\n\trep movsb\n\tcld" : "+D"(dd), "+S"(ss), "+c"(n) : : "memory");
    return d;
}

void *g_memset(void *d, int c, size_t n) {
    void *r = d;
    __asm__ volatile("rep stosb" : "+D"(d), "+c"(n) : "a"(c) : "memory");
    return r;
}

int g_memcmp(const void *a, const void *b, size_t n) {
    const u8 *x = (const u8 *)a, *y = (const u8 *)b;
    while (n--) { if (*x != *y) return (int)*x - (int)*y; x++; y++; }
    return 0;
}

void *g_memchr(const void *s, int c, size_t n) {
    const u8 *p = (const u8 *)s;
    while (n--) { if (*p == (u8)c) return (void *)p; p++; }
    return 0;
}

size_t g_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return (size_t)(p - s);
}

char *g_strcpy(char *d, const char *s) {
    char *r = d;
    while ((*d++ = *s++)) ;
    return r;
}

char *g_strncpy(char *d, const char *s, size_t n) {
    char *r = d;
    while (n && (*d = *s)) { d++; s++; n--; }
    while (n--) *d++ = 0;
    return r;
}

char *g_strcat(char *d, const char *s) {
    char *r = d;
    while (*d) d++;
    while ((*d++ = *s++)) ;
    return r;
}

int g_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (int)(u8)*a - (int)(u8)*b;
}

int g_strncmp(const char *a, const char *b, size_t n) {
    while (n && *a && *a == *b) { a++; b++; n--; }
    if (!n) return 0;
    return (int)(u8)*a - (int)(u8)*b;
}

char *g_strchr(const char *s, int c) {
    for (;; s++) {
        if (*s == (char)c) return (char *)s;
        if (!*s) return 0;
    }
}

char *g_strrchr(const char *s, int c) {
    const char *r = 0;
    for (;; s++) {
        if (*s == (char)c) r = s;
        if (!*s) return (char *)r;
    }
}

char *g_strstr(const char *h, const char *n) {
    if (!*n) return (char *)h;
    for (; *h; h++) {
        const char *a = h, *b = n;
        while (*a && *b && *a == *b) { a++; b++; }
        if (!*b) return (char *)h;
    }
    return 0;
}

/* ---- allocator: 16-byte header {size}, power-of-two-ish bins ---- */
static unsigned class_of(size_t n, size_t *rounded) {
    size_t r = 16; unsigned c = 0;
    if (n <= 512) { r = (n + 15) & ~15u; c = r / 16; }              /* classes 1..32 */
    else { r = 1024; c = 33; while (r < n) { r <<= 1; c++; } }       /* 33.. */
    if (r == 0) { r = 16; c = 1; }
    *rounded = r;
    return c;
}

static inline void heap_lock(void) { while (__sync_lock_test_and_set(&ST->lock, 1)) { } }
static inline void heap_unlock(void) { __sync_lock_release(&ST->lock); }

static void *malloc_locked(size_t n) {
    size_t r; unsigned c = class_of(n ? n : 1, &r);
    struct state *s = ST;
    unsigned *blk = (unsigned *)s->bins[c];
    if (blk) {
        s->bins[c] = blk[1];             /* next pointer in payload word 1: word 0 (a vtable pointer, say) stays intact */
        g_memset(blk, 0, r);             /* recycled memory comes back zeroed like fresh pages: the game relies on that (stale state after a race was quit) */
        return (void *)(blk);
    }
    unsigned p = s->heap_ptr;
    if (p + r + 16 > s->heap_end) return 0;
    s->heap_ptr = p + r + 16;
    *(unsigned *)(p + 12) = c;          /* header: class at [-4], size at [-8] */
    *(unsigned *)(p + 8) = (unsigned)r;
    return (void *)(p + 16);
}

void *g_malloc(size_t n) { heap_lock(); void *p = malloc_locked(n); heap_unlock(); return p; }

void g_free(void *p) {
    unsigned a = (unsigned)p;
    if (!p) return;
    if (a < ARENA_LO) {                  /* python-side block (strings, stream buffers made by the host): give it back to python */
        if (a >= 0x20000000u && ST->slow_unlock) ((void (*)(void *))ST->slow_unlock)(p);
        return;
    }
    unsigned c = *(unsigned *)(a - 4);
    struct state *s = ST;
    heap_lock();
    /* A freed block is not reused right away: the game has benign-in-practice use-after-free races (the audio thread
     * still walking an object the main thread just freed). Quarantine it, and keep its first word untouched. */
    s->qbytes += *(unsigned *)(a - 8);
    QRING[s->qhead++ & (QN - 1)] = a;
    while (s->qhead - s->qtail > QN || s->qbytes > QMAX_BYTES) {
        unsigned old = QRING[s->qtail++ & (QN - 1)];
        unsigned oc = *(unsigned *)(old - 4);
        s->qbytes -= *(unsigned *)(old - 8);
        ((unsigned *)old)[1] = s->bins[oc];
        s->bins[oc] = old;
    }
    heap_unlock();
}

void *g_calloc(size_t a, size_t b) {
    size_t n = a * b;
    void *p = g_malloc(n);
    if (p) g_memset(p, 0, n);
    return p;
}

void *g_realloc(void *p, size_t n) {
    if (!p) return g_malloc(n);
    unsigned a = (unsigned)p;
    if (a < ARENA_LO) {              /* python-side block: its size is in the header ([a-8] size, [a-4] magic) */
        unsigned osz = (a >= 0x20000010u && *(unsigned *)(a - 4) == 0x50594D41u) ? *(unsigned *)(a - 8) : n;
        void *q = g_malloc(n);
        if (q) g_memcpy(q, p, osz < n ? osz : n);
        g_free(p);
        return q;
    }
    unsigned old = *(unsigned *)(a - 8);
    if (n <= old) return p;
    void *q = g_malloc(n);
    if (!q) return 0;
    g_memcpy(q, p, old);
    g_free(p);
    return q;
}

/* ---- math (x87) ---- */
double g_fmax(double a, double b) { return (a != a) ? b : (b != b) ? a : (a > b ? a : b); }
double g_fmin(double a, double b) { return (a != a) ? b : (b != b) ? a : (a < b ? a : b); }
static double round_mode(double x, unsigned short mode) {
    unsigned short cw, nw;
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    nw = (cw & 0xF3FF) | mode;
    __asm__ volatile("fldcw %0" : : "m"(nw));
    __asm__ volatile("frndint" : "+t"(x));
    __asm__ volatile("fldcw %0" : : "m"(cw));
    return x;
}
double g_floor(double x) { return round_mode(x, 0x0400); }
double g_ceil(double x) { return round_mode(x, 0x0800); }
float g_floorf(float x) { return (float)round_mode((double)x, 0x0400); }
float g_ceilf(float x) { return (float)round_mode((double)x, 0x0800); }
double g_sin(double x) { double r; __asm__("fsin" : "=t"(r) : "0"(x)); return r; }
double g_cos(double x) { double r; __asm__("fcos" : "=t"(r) : "0"(x)); return r; }
double g_tan(double x) { double r; __asm__("fptan; fstp %%st(0)" : "=t"(r) : "0"(x)); return r; }
float g_sinf(float x) { return (float)g_sin((double)x); }
float g_cosf(float x) { return (float)g_cos((double)x); }
float g_tanf(float x) { return (float)g_tan((double)x); }
double g_atan2(double y, double x) { double r; __asm__("fpatan" : "=t"(r) : "0"(x), "u"(y) : "st(1)"); return r; }
float g_atan2f(float y, float x) { return (float)g_atan2((double)y, (double)x); }

/* ---- ctype-ish ---- */
int g_tolower(int c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; }
int g_toupper(int c) { return (c >= 'a' && c <= 'z') ? c - 32 : c; }
int g_strcasecmp(const char *a, const char *b) {
    for (;;) {
        int x = g_tolower((u8)*a), y = g_tolower((u8)*b);
        if (x != y) return x - y;
        if (!x) return 0;
        a++; b++;
    }
}

/* ---- mutexes: state lives in the pthread_mutex_t itself: sig, owner (thread id, 0 = free), count. Contended locks spin;
 * condition variables (python side) release/re-acquire through the same fields. ---- */
struct mtx { unsigned sig, owner, count; };
static inline unsigned my_tid(void) {
    unsigned esp; __asm__ volatile("movl %%esp,%0" : "=r"(esp));
    return ((unsigned *)(((esp | 0x7FFFFFu) + 1u) - 0x400u))[0x50 / 4];
}
int g_pthread_mutex_lock(struct mtx *m) {
    unsigned me = my_tid();
    if (m->owner == me) { m->count++; return 0; }
    while (__sync_val_compare_and_swap(&m->owner, 0, me) != 0) __asm__ volatile("pause");
    m->count = 1;
    return 0;
}
int g_pthread_mutex_trylock(struct mtx *m) {
    unsigned me = my_tid();
    if (m->owner == me) { m->count++; return 0; }
    if (__sync_val_compare_and_swap(&m->owner, 0, me) != 0) return 16;
    m->count = 1;
    return 0;
}
int g_pthread_mutex_unlock(struct mtx *m) {
    if (m->owner != my_tid()) return 0;
    if (--m->count > 0) return 0;
    m->count = 0;
    __asm__ volatile("" ::: "memory");
    m->owner = 0;
    return 0;
}

/* ---- libstdc++ 4.2 copy-on-write std::string basics (rep header {len, cap, refcount} precedes the characters) ---- */
#define EMPTYP (*(unsigned *)0xE00A0100)
static void s_set(unsigned *self, const char *src, unsigned n) {
    unsigned old = *self, nw;
    if (n == 0) nw = EMPTYP;
    else {
        char *m = (char *)g_malloc(n + 1 + 12 + 4);
        ((unsigned *)m)[0] = n; ((unsigned *)m)[1] = n; ((unsigned *)m)[2] = 0;
        g_memcpy(m + 12, src, n); m[12 + n] = 0;
        nw = (unsigned)(m + 12);
    }
    if (old != EMPTYP && ((unsigned *)old)[-1] <= 0x7FFFFFFFu) g_free((void *)(old - 12));
    *self = nw;
}
static inline unsigned s_len(const unsigned *s) { return ((unsigned *)*s)[-3]; }
unsigned g__ZNSsC1Ev(unsigned *self) { *self = EMPTYP; return (unsigned)self; }
unsigned g__ZNSsC1EPKcRKSaIcE(unsigned *self, const char *c, void *al) { *self = EMPTYP; s_set(self, c, g_strlen(c)); return (unsigned)self; }
unsigned g__ZNSsC1ERKSs(unsigned *self, const unsigned *o) { *self = EMPTYP; s_set(self, (const char *)*o, s_len(o)); return (unsigned)self; }
void g__ZNSsD1Ev(unsigned *self) { unsigned p = *self; if (p != EMPTYP) g_free((void *)(p - 12)); }
void g__ZNSs4_Rep10_M_destroyERKSaIcE(void *rep, void *al) { g_free(rep); }
unsigned g__ZNSs6assignEPKcm(unsigned *self, const char *c, unsigned n) { s_set(self, c, n); return (unsigned)self; }
unsigned g__ZNSs6assignERKSs(unsigned *self, const unsigned *o) { s_set(self, (const char *)*o, s_len(o)); return (unsigned)self; }
unsigned g__ZNSsaSERKSs(unsigned *self, const unsigned *o) { s_set(self, (const char *)*o, s_len(o)); return (unsigned)self; }
unsigned g__ZNSsaSEPKc(unsigned *self, const char *c) { s_set(self, c, g_strlen(c)); return (unsigned)self; }
unsigned g__ZNKSs4sizeEv(const unsigned *self) { return s_len(self); }
unsigned g__ZNKSs6lengthEv(const unsigned *self) { return s_len(self); }
unsigned g__ZNKSs5emptyEv(const unsigned *self) { return s_len(self) == 0; }
unsigned g__ZNKSs5c_strEv(const unsigned *self) { return *self; }
unsigned g__ZNKSsixEm(const unsigned *self, unsigned i) { return *self + i; }

/* ---- red/black tree iteration: node = {color, parent, left, right} ---- */
struct rbn { unsigned color; struct rbn *parent, *left, *right; };
struct rbn *g__ZSt18_Rb_tree_incrementPSt18_Rb_tree_node_base(struct rbn *n) {
    if (n->right) { n = n->right; while (n->left) n = n->left; }
    else { struct rbn *y = n->parent; while (n == y->right) { n = y; y = y->parent; } if (n->right != y) n = y; }
    return n;
}
struct rbn *g__ZSt18_Rb_tree_incrementPKSt18_Rb_tree_node_base(struct rbn *n) { return g__ZSt18_Rb_tree_incrementPSt18_Rb_tree_node_base(n); }
struct rbn *g__ZSt18_Rb_tree_decrementPSt18_Rb_tree_node_base(struct rbn *n) {
    if (n->color == 0 && n->parent->parent == n) n = n->right;
    else if (n->left) { struct rbn *y = n->left; while (y->right) y = y->right; n = y; }
    else { struct rbn *y = n->parent; while (n == y->left) { n = y; y = y->parent; } n = y; }
    return n;
}

/* ---- asynchronous GL command ring (producer side). Fire-and-forget GL calls are queued for the host servicer, which
 * replays them in order while the game keeps running. Generic calls enter through g_gl_stub (eax = hook index);
 * calls that change client-array bookkeeping, and the draw calls, are real guest functions below. ---- */
#define RING_WORDS 0x100000u
#define DESC_TABLE ((volatile unsigned char *)0xE0090000)
struct rctx {
    unsigned char _p0[0x70]; unsigned ring_base; unsigned char _p1[0x0C];
    volatile unsigned rhead; unsigned char _p2[0x3C]; volatile unsigned rtail;
};
static inline struct rctx *my_ctx(void) {
    unsigned esp; __asm__ volatile("movl %%esp,%0" : "=r"(esp));
    return (struct rctx *)(((esp | 0x7FFFFFu) + 1u) - 0x400u);
}
/* reserve `need` contiguous words; returns the slot, *headp = head to commit */
static volatile unsigned *rq_begin(struct rctx *c, unsigned need, unsigned *headp) {
    volatile unsigned *ring = (volatile unsigned *)c->ring_base;
    unsigned head = c->rhead, off = head & (RING_WORDS - 1);
    if (off + need > RING_WORDS) {                       /* does not fit before the end: pad and wrap */
        unsigned padn = RING_WORDS - off;
        while (head + padn - c->rtail > RING_WORDS) __asm__ volatile("pause");
        if (padn >= 2) { ring[off] = 0xFFFFFFFFu; ring[off + 1] = padn; }
        __asm__ volatile("" ::: "memory");
        head += padn; c->rhead = head; off = 0;
    }
    while (head + need - c->rtail > RING_WORDS) __asm__ volatile("pause");
    *headp = head;
    return ring + off;
}
static inline void rq_commit(struct rctx *c, unsigned head, unsigned need) {
    __asm__ volatile("" ::: "memory");
    c->rhead = head + need;
}
static void rec_plain(unsigned idx, unsigned n, const unsigned *args) {
    struct rctx *c = my_ctx(); unsigned head, need = 2 + n;
    volatile unsigned *q = rq_begin(c, need, &head);
    q[0] = idx; q[1] = need;
    for (unsigned i = 0; i < n; i++) q[2 + i] = args[i];
    rq_commit(c, head, need);
}
void g_gl_rec(unsigned idx, unsigned *args) {
    volatile unsigned char *d = DESC_TABLE + idx * 4;
    unsigned n = d[0], pa = d[1], ca = d[2], wpe = d[3], pw = 0;
    if (pa == 0xFF) { rec_plain(idx, n, args); return; }
    unsigned cnt = ca == 0xFF ? 1 : args[ca];
    if (cnt > 1024) cnt = 1024;
    pw = cnt * wpe;
    struct rctx *c = my_ctx(); unsigned head, need = 2 + n + pw;
    volatile unsigned *q = rq_begin(c, need, &head);
    q[0] = idx; q[1] = need;
    for (unsigned i = 0; i < n; i++) q[2 + i] = args[i];
    const unsigned *src = (const unsigned *)args[pa];
    volatile unsigned *dst = q + 2 + n;
    for (unsigned i = 0; i < pw; i++) dst[i] = src[i];
    q[2 + pa] = (unsigned)dst;
    rq_commit(c, head, need);
}
__asm__(".text\n.globl _g_gl_stub\n_g_gl_stub:\n\tleal 4(%esp), %edx\n\tpushl %edx\n\tpushl %eax\n\tcall _g_gl_rec\n\taddl $8, %esp\n\tret\n");

/* mirror of the vertex-array state, needed to snapshot client arrays at draw time */
struct att { unsigned size, typ, norm, stride, ptr, client; };
struct gls {
    unsigned array_buf, elem_buf, enabled;
    unsigned i_bind, i_enable, i_disable, i_vap, i_de, i_da, i_dc, f_de, f_da;
    struct att a[16];
};
#define GLS ((struct gls *)0xE00A0000)

void g_glBindBuffer(unsigned target, unsigned buf) {
    if (target == 0x8892) GLS->array_buf = buf; else if (target == 0x8893) GLS->elem_buf = buf;
    unsigned a[2] = { target, buf }; rec_plain(GLS->i_bind, 2, a);
}
void g_glEnableVertexAttribArray(unsigned i) {
    if (i < 16) GLS->enabled |= 1u << i;
    rec_plain(GLS->i_enable, 1, &i);
}
void g_glDisableVertexAttribArray(unsigned i) {
    if (i < 16) GLS->enabled &= ~(1u << i);
    rec_plain(GLS->i_disable, 1, &i);
}
void g_glVertexAttribPointer(unsigned i, unsigned size, unsigned typ, unsigned norm, unsigned stride, unsigned ptr) {
    if (i < 16) {
        struct att *t = &GLS->a[i];
        t->size = size; t->typ = typ; t->norm = norm & 0xFF; t->stride = stride; t->ptr = ptr; t->client = GLS->array_buf == 0;
    }
    unsigned a[6] = { i, size, typ, norm, stride, ptr }; rec_plain(GLS->i_vap, 6, a);
}

static unsigned type_size(unsigned t) {
    if (t == 0x1400 || t == 0x1401) return 1;
    if (t == 0x1402 || t == 0x1403) return 2;
    if (t == 0x140A) return 8;
    return 4;
}
#define MAX_DRAW_WORDS 0x40000u      /* larger snapshots go the slow synchronous way */

/* queue a draw with client arrays: [kind, mode, a, count, typ, idxptr, nattr, nattr * {idx,size,typ,norm,stride,base}, payload] */
static int queue_draw(unsigned kind, unsigned mode, unsigned a, unsigned count, unsigned typ, const unsigned char *indices,
                      unsigned lo, unsigned hi) {
    struct gls *g = GLS;
    unsigned nat = 0, bytes = 0, i;
    unsigned vn = hi - lo + 1;
    for (i = 0; i < 16; i++) if ((g->enabled >> i & 1) && g->a[i].client) {
        struct att *t = &g->a[i];
        unsigned st = t->stride ? t->stride : t->size * type_size(t->typ);
        bytes += (vn * st + 15) & ~15u; nat++;
    }
    unsigned isz = kind ? type_size(typ) : 0, ibytes = kind ? ((count * isz + 15) & ~15u) : 0;
    unsigned pw = (bytes + ibytes) / 4, hw = 7 + nat * 6, need = 2 + hw + pw;
    if (need > MAX_DRAW_WORDS) return 0;
    struct rctx *c = my_ctx(); unsigned head;
    volatile unsigned *q = rq_begin(c, need, &head);
    q[0] = g->i_dc; q[1] = need;
    volatile unsigned *h = q + 2;
    unsigned char *pay = (unsigned char *)(h + hw);
    h[0] = kind; h[1] = mode; h[2] = a; h[3] = count; h[4] = typ; h[6] = nat;
    if (kind) { g_memcpy(pay, indices, count * isz); h[5] = (unsigned)pay; pay += ibytes; } else h[5] = 0;
    unsigned k = 0;
    for (i = 0; i < 16; i++) if ((g->enabled >> i & 1) && g->a[i].client) {
        struct att *t = &g->a[i];
        unsigned st = t->stride ? t->stride : t->size * type_size(t->typ);
        g_memcpy(pay, (const void *)(t->ptr + lo * st), vn * st);
        volatile unsigned *e = h + 7 + k * 6;
        e[0] = i; e[1] = t->size; e[2] = t->typ; e[3] = t->norm; e[4] = st; e[5] = (unsigned)pay - lo * st;
        pay += (vn * st + 15) & ~15u; k++;
    }
    rq_commit(c, head, need);
    return 1;
}
static int has_client(void) {
    for (unsigned i = 0; i < 16; i++) if ((GLS->enabled >> i & 1) && GLS->a[i].client) return 1;
    return 0;
}
void g_glDrawArrays(unsigned mode, int first, int count) {
    unsigned a[3] = { mode, (unsigned)first, (unsigned)count };
    if (count <= 0) return;
    if (!has_client()) { rec_plain(GLS->i_da, 3, a); return; }
    if (!queue_draw(0, mode, (unsigned)first, (unsigned)count, 0, 0, (unsigned)first, (unsigned)first + count - 1))
        ((void (*)(unsigned, int, int))GLS->f_da)(mode, first, count);
}
void g_glDrawElements(unsigned mode, int count, unsigned typ, unsigned ptr) {
    unsigned a[4] = { mode, (unsigned)count, typ, ptr };
    if (count <= 0) return;
    int client = has_client();
    if (GLS->elem_buf) { if (!client) rec_plain(GLS->i_de, 4, a); return; }     /* client vertices + VBO indices: unused by the game */
    unsigned isz = type_size(typ), lo = 0xFFFFFFFFu, hi = 0, i;
    const unsigned char *p = (const unsigned char *)ptr;
    if (isz == 2) { const unsigned short *s = (const unsigned short *)p; for (i = 0; i < (unsigned)count; i++) { unsigned v = s[i]; if (v < lo) lo = v; if (v > hi) hi = v; } }
    else if (isz == 4) { const unsigned *s = (const unsigned *)p; for (i = 0; i < (unsigned)count; i++) { unsigned v = s[i]; if (v < lo) lo = v; if (v > hi) hi = v; } }
    else { for (i = 0; i < (unsigned)count; i++) { unsigned v = p[i]; if (v < lo) lo = v; if (v > hi) hi = v; } }
    if (!queue_draw(1, mode, 0, (unsigned)count, typ, p, lo, hi))
        ((void (*)(unsigned, int, unsigned, unsigned))GLS->f_de)(mode, count, typ, ptr);
}

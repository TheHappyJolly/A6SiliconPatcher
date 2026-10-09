typedef unsigned int size_t;
typedef int (*cmp_t)(const void*, const void*);
static void swp(char *a, char *b, size_t n){ while(n--){ char t=*a; *a++=*b; *b++=t; } }
void g_qsort(void *base, size_t n, size_t sz, cmp_t cmp){
    char *b = (char*)base;
    /* iterative-friendly recursive quicksort with insertion sort for small */
    while (n > 8) {
        char *p = b + (n/2)*sz;
        swp(p, b + (n-1)*sz, sz);
        char *store = b;
        for (char *i = b; i < b + (n-1)*sz; i += sz) {
            if (cmp(i, b + (n-1)*sz) < 0) { swp(i, store, sz); store += sz; }
        }
        swp(store, b + (n-1)*sz, sz);
        size_t left = (store - b)/sz;
        size_t right = n - left - 1;
        if (left < right) { g_qsort(b, left, sz, cmp); b = store + sz; n = right; }
        else { g_qsort(store + sz, right, sz, cmp); n = left; }
    }
    for (size_t i = 1; i < n; i++)
        for (size_t j = i; j > 0 && cmp(b + (j-1)*sz, b + j*sz) > 0; j--) swp(b + (j-1)*sz, b + j*sz, sz);
}

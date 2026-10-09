typedef unsigned int size_t;
typedef int (*cmp_t)(const void*, const void*);
extern void g_qsort(void*, size_t, size_t, cmp_t);
int cmpf(const void *a, const void *b){ float x=*(const float*)a, y=*(const float*)b; return x<y?-1:(x>y?1:0); }
void g_test(float *arr, size_t n){ g_qsort(arr, n, 4, cmpf); }

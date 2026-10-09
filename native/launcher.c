/* x86_64 launcher: a non-PIE executable with a tiny __PAGEZERO and a reserved __GUEST segment covering
 * 0x1000..0x2000000, so the 32-bit guest image can be mapped at its original low addresses; __GUEST/__GUEST2 reserve the rest of
 * the low 4 GB at exec time so no library or allocation lands there. Then it runs the embedded Python. */
#include <Python.h>
#include <sys/mman.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <limits.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX]; uint32_t sz = sizeof exe;
    _NSGetExecutablePath(exe, &sz);
    char real[PATH_MAX]; realpath(exe, real);
    char home[PATH_MAX]; snprintf(home, sizeof home, "%s/python", getenv("A6_PYHOME") ? getenv("A6_PYHOME") : dirname(real));
    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    return Py_BytesMain(argc, argv);
}

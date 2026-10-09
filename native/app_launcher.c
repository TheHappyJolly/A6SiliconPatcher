/* Native (arm64/x86_64 universal) main executable of the app bundle. LaunchServices needs a real, signed Mach-O here
 * (a shell script as CFBundleExecutable is not reliably launchable on every macOS version). It checks for Rosetta 2 and
 * starts the 32-bit-capable x86_64 engine through /usr/bin/arch. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>
extern char **environ;

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) != 0 || !realpath(exe, real)) return 2;
    char *slash = strrchr(real, '/'); if (slash) *slash = 0;             /* .../Contents/MacOS */
    char contents[PATH_MAX], app[PATH_MAX], eng[PATH_MAX], launcher[PATH_MAX], script[PATH_MAX], pyhome[PATH_MAX];
    snprintf(contents, sizeof contents, "%s/..", real);
    char cr[PATH_MAX]; if (!realpath(contents, cr)) return 2;
    snprintf(app, sizeof app, "%s/..", cr);
    char ar[PATH_MAX]; if (!realpath(app, ar)) return 2;
    snprintf(eng, sizeof eng, "%s/Resources/engine", cr);
    snprintf(launcher, sizeof launcher, "%s/native/a6launcher", eng);
    snprintf(script, sizeof script, "%s/n6main.py", eng);
    snprintf(pyhome, sizeof pyhome, "%s/x64", eng);

    /* Rosetta 2 present? */
    pid_t p; int st = 1;
    char *chk[] = { "/usr/bin/arch", "-x86_64", "/usr/bin/true", NULL };
    if (posix_spawn(&p, chk[0], NULL, NULL, chk, environ) == 0) { waitpid(p, &st, 0); }
    if (st != 0) {
        system("/usr/bin/osascript -e 'display dialog \"Asphalt 6 requires Rosetta 2.\\n\\nInstall it in Terminal with:\\nsoftwareupdate --install-rosetta --agree-to-license\" buttons {\"OK\"} with icon caution' >/dev/null 2>&1");
        return 1;
    }
    setenv("A6_BUNDLE", ar, 1);
    setenv("A6_PYHOME", pyhome, 1);
    const char *home = getenv("HOME");
    if (home) {
        char dir[PATH_MAX], log[PATH_MAX];
        snprintf(dir, sizeof dir, "%s/Library/Logs", home); mkdir(dir, 0755);
        snprintf(log, sizeof log, "%s/Asphalt6-arm64.log", dir);
        struct stat sb; char old[PATH_MAX];
        if (stat(log, &sb) == 0 && sb.st_size > 5 * 1024 * 1024) { snprintf(old, sizeof old, "%s.1", log); rename(log, old); }   /* keep the log bounded */
        int fd = open(log, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
    }
    char *args[] = { "arch", "-x86_64", launcher, script, NULL };
    execv("/usr/bin/arch", args);
    return 3;
}

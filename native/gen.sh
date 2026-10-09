cd "$(dirname "$0")"
clang -target x86_64-apple-macos11 -c gtramp.s -o gtramp.o && ../venv/bin/python - <<'PY'
import lief
o=lief.parse("gtramp.o")
b=bytes([s for s in o.sections if s.name=="__text"][0].content)
def arr(n,d): return "static const uint8_t %s[%d] = {%s};\n"%(n,len(d),",".join(str(x) for x in d))
open("gtramp_blob.h","w").write(arr("blob_park64",b[0x300:0x340])+arr("blob_guest32",b[0x2000:0x2310]))
PY
clang -arch x86_64 -shared -O2 -framework OpenGL -o libhost.dylib host.c

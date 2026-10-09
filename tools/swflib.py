import struct, os
class BitR:
    def __init__(s,b,p=0): s.b=b; s.p=p; s.bit=0
    def ub(s,n):
        v=0
        for _ in range(n):
            v=(v<<1)|((s.b[s.p]>>(7-s.bit))&1); s.bit+=1
            if s.bit==8: s.bit=0; s.p+=1
        return v
    def sb(s,n):
        v=s.ub(n)
        if n and v>>(n-1): v-=1<<n
        return v
    def align(s):
        if s.bit: s.bit=0; s.p+=1
def rect(b,p):
    r=BitR(b,p); n=r.ub(5); vals=[r.sb(n) for _ in range(4)]; r.align(); return vals,r.p
def matrix(r):
    sx=sy=1.0; r0=r1=0.0
    if r.ub(1): n=r.ub(5); sx=r.sb(n)/65536; sy=r.sb(n)/65536
    if r.ub(1): n=r.ub(5); r0=r.sb(n)/65536; r1=r.sb(n)/65536
    n=r.ub(5); tx=r.sb(n); ty=r.sb(n); r.align()
    return (sx,sy,r0,r1,tx,ty)
def tags(b,p,end):
    while p<end:
        h=struct.unpack_from('<H',b,p)[0]; t=h>>6; l=h&0x3f; q=p+2
        if l==0x3f: l=struct.unpack_from('<I',b,q)[0]; q+=4
        yield t,p,q,l
        p=q+l
        if t==0 and False: break
def cstr(b,p):
    j=b.index(b'\0',p); return b[p:j].decode('latin1'),j+1
def place2(b,q,l):
    fl=b[q]; p=q+1; depth=struct.unpack_from('<H',b,p)[0]; p+=2
    d={'depth':depth,'flags':fl}
    if fl&2: d['char']=struct.unpack_from('<H',b,p)[0]; p+=2
    if fl&4:
        r=BitR(b,p); d['matrix']=matrix(r); p=r.p
    if fl&8:
        # cxform
        r=BitR(b,p); has_add=r.ub(1); has_mul=r.ub(1); n=r.ub(4)
        if has_mul: [r.sb(n) for _ in range(4)]
        if has_add: [r.sb(n) for _ in range(4)]
        r.align(); p=r.p
    if fl&0x10: d['ratio']=struct.unpack_from('<H',b,p)[0]; p+=2
    if fl&0x20: d['name'],p=cstr(b,p)
    if fl&0x40: d['clipdepth']=struct.unpack_from('<H',b,p)[0]; p+=2
    d['end']=p
    return d

def load(path=os.path.expanduser('~/Downloads/Asphalt 6.app/Contents/Resources/data/file000516.dat')):
    b=open(path,'rb').read()
    r,p=rect(b,8); p+=4
    sprites={}; exports={}; top=[]
    for t,s,q,l in tags(b,p,len(b)):
        if t==56:
            cnt=struct.unpack_from('<H',b,q)[0]; pp=q+2
            for _ in range(cnt):
                cid=struct.unpack_from('<H',b,pp)[0]; nm,pp=cstr(b,pp+2); exports[cid]=nm
        if t==39:
            cid,fr=struct.unpack_from('<HH',b,q); sprites[cid]=(s,q,l,fr)
    return b,p,sprites,exports
def sprite_tags(b,sprites,cid):
    s,q,l,fr=sprites[cid]
    return list(tags(b,q+4,q+l))

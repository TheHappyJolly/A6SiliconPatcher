import struct
def s(x): return b'\x00'+x.encode()+b'\x00'
def f(x): return b'\x01'+struct.pack('<f',x)
def i(x): return b'\x07'+struct.pack('<i',x)
def push(*items):
    d=b''.join(items); return b'\x96'+struct.pack('<H',len(d))+d
def jump(off): return b'\x99\x02\x00'+struct.pack('<h',off)
GETVAR=b'\x1c'; SETVAR=b'\x1d'; POP=b'\x17'; CLONE=b'\x24'; SETPROP=b'\x23'; GETPROP=b'\x22'; GETMEMBER=b'\x4e'; SETMEMBER=b'\x4f'; CALLMETHOD=b'\x52'; CALLFUNC=b'\x3d'; TRACE=b'\x26'

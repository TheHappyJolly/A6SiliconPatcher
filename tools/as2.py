import struct
OP={0x04:'NextFrame',0x05:'PrevFrame',0x06:'Play',0x07:'Stop',0x08:'ToggleQuality',0x09:'StopSounds',0x0a:'Add',0x0b:'Subtract',0x0c:'Multiply',0x0d:'Divide',0x0e:'Equals',0x0f:'Less',0x10:'And',0x11:'Or',0x12:'Not',0x13:'StringEquals',0x14:'StringLength',0x15:'StringExtract',0x17:'Pop',0x18:'ToInteger',0x1c:'GetVariable',0x1d:'SetVariable',0x20:'SetTarget2',0x21:'StringAdd',0x22:'GetProperty',0x23:'SetProperty',0x24:'CloneSprite',0x25:'RemoveSprite',0x26:'Trace',0x27:'StartDrag',0x28:'EndDrag',0x29:'StringLess',0x2a:'Throw',0x2b:'CastOp',0x2c:'ImplementsOp',0x30:'RandomNumber',0x31:'MBStringLength',0x32:'CharToAscii',0x33:'AsciiToChar',0x34:'GetTime',0x35:'MBStringExtract',0x36:'MBCharToAscii',0x37:'MBAsciiToChar',0x3a:'Delete',0x3b:'Delete2',0x3c:'DefineLocal',0x3d:'CallFunction',0x3e:'Return',0x3f:'Modulo',0x40:'NewObject',0x41:'DefineLocal2',0x42:'InitArray',0x43:'InitObject',0x44:'TypeOf',0x45:'TargetPath',0x46:'Enumerate',0x47:'Add2',0x48:'Less2',0x49:'Equals2',0x4a:'ToNumber',0x4b:'ToString',0x4c:'PushDuplicate',0x4d:'StackSwap',0x4e:'GetMember',0x4f:'SetMember',0x50:'Increment',0x51:'Decrement',0x52:'CallMethod',0x53:'NewMethod',0x54:'InstanceOf',0x55:'Enumerate2',0x60:'BitAnd',0x61:'BitOr',0x62:'BitXor',0x63:'BitLShift',0x64:'BitRShift',0x65:'BitURShift',0x66:'StrictEquals',0x67:'Greater',0x68:'StringGreater',0x69:'Extends',
0x81:'GotoFrame',0x83:'GetURL',0x87:'StoreRegister',0x88:'ConstantPool',0x8a:'WaitForFrame',0x8b:'SetTarget',0x8c:'GotoLabel',0x8d:'WaitForFrame2',0x8e:'DefineFunction2',0x94:'With',0x96:'Push',0x99:'Jump',0x9a:'GetURL2',0x9b:'DefineFunction',0x9d:'If',0x9e:'Call',0x9f:'GotoFrame2'}
def cstr(b,i):
    j=b.index(b'\0',i); return b[i:j].decode('latin1'),j+1
def disasm(b,start=0,end=None,pool=None,indent=0,out=None,base=0):
    end=end or len(b); i=start; pool=pool if pool is not None else []
    lines=[]
    while i<end:
        op=b[i]; pos=i; i+=1
        if op>=0x80:
            ln=struct.unpack_from('<H',b,i)[0]; i+=2; d=b[i:i+ln]
            name=OP.get(op,f'op{op:#x}')
            txt=name
            if op==0x88:
                cnt=struct.unpack_from('<H',d,0)[0]; p=2; pool[:]=[]
                for _ in range(cnt):
                    s,p=cstr(d,p); pool.append(s)
                txt+=f' [{cnt} consts]'
            elif op==0x96:
                p=0; vals=[]
                while p<len(d):
                    t=d[p]; p+=1
                    if t==0: s,p=cstr(d,p); vals.append(repr(s))
                    elif t==1: vals.append(f'f{struct.unpack_from("<f",d,p)[0]:g}'); p+=4
                    elif t==2: vals.append('null')
                    elif t==3: vals.append('undef')
                    elif t==4: vals.append(f'reg{d[p]}'); p+=1
                    elif t==5: vals.append('true' if d[p] else 'false'); p+=1
                    elif t==6: vals.append(f'd{struct.unpack_from("<d",d,p)[0]:g}'); p+=8
                    elif t==7: vals.append(f'i{struct.unpack_from("<i",d,p)[0]}'); p+=4
                    elif t==8: vals.append(f'C[{d[p]}]={pool[d[p]]!r}' if d[p]<len(pool) else f'C[{d[p]}]?'); p+=1
                    elif t==9: v=struct.unpack_from('<H',d,p)[0]; vals.append(f'C[{v}]={pool[v]!r}' if v<len(pool) else f'C[{v}]?'); p+=2
                    else: vals.append(f'?t{t}'); break
                txt+=' '+', '.join(vals)
            elif op==0x9b:
                nm,p=cstr(d,0); np_=struct.unpack_from('<H',d,p)[0]; p+=2; params=[]
                for _ in range(np_): s,p=cstr(d,p); params.append(s)
                cs=struct.unpack_from('<H',d,p)[0]
                txt+=f' {nm}({",".join(params)}) size {cs}'
                lines.append(f'{pos:#06x}: {"  "*indent}{txt}')
                i+=ln
                lines+=disasm(b,i,i+cs,pool,indent+1)
                i+=cs; continue
            elif op==0x8e:
                nm,p=cstr(d,0); np_=struct.unpack_from('<H',d,p)[0]; p+=2; nreg=d[p]; p+=1; flags=struct.unpack_from('<H',d,p)[0]; p+=2
                params=[]
                for _ in range(np_): r=d[p]; p+=1; s,p=cstr(d,p); params.append(f'{s}@r{r}')
                cs=struct.unpack_from('<H',d,p)[0]
                txt+=f' {nm}({",".join(params)}) regs={nreg} flags={flags:#x} size {cs}'
                lines.append(f'{pos:#06x}: {"  "*indent}{txt}')
                i+=ln
                lines+=disasm(b,i,i+cs,pool,indent+1)
                i+=cs; continue
            elif op in (0x99,0x9d):
                off=struct.unpack_from('<h',d,0)[0]; txt+=f' -> {i+ln+off:#06x}'
            elif op==0x87: txt+=f' reg{d[0]}'
            else: txt+=' '+d.hex()
            lines.append(f'{pos:#06x}: {"  "*indent}{txt}')
            i+=ln
        else:
            lines.append(f'{pos:#06x}: {"  "*indent}{OP.get(op,f"op{op:#x}")}')
    return lines

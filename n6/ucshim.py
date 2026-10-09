"""Tiny stand-in for unicorn.x86_const / Uc so the existing HLE modules run unchanged on the native runtime."""
UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX = 1, 2, 3, 4
UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI = 5, 6, 7, 8
UC_X86_REG_EIP = 9
UC_X86_REG_FPCW = UC_X86_REG_FPTAG = UC_X86_REG_FPSW = UC_X86_REG_MXCSR = 100
UC_X86_REG_FP0 = 200
UC_HOOK_CODE = UC_HOOK_BLOCK = UC_HOOK_MEM_WRITE = 0
class UcError(Exception): pass

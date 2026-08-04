import sys, dis, marshal

with open(r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res\scripts\client\AvatarInputHandler\cameras.pyc', 'rb') as f:
    f.read(8) # skip magic and timestamp
    code = marshal.load(f)

def dump_code(c, prefix=''):
    print(prefix + "Name:", c.co_name)
    if c.co_name in ('SniperCamera', 'update'):
        dis.dis(c)
    for const in c.co_consts:
        if hasattr(const, 'co_code'):
            dump_code(const, prefix + '  ')

dump_code(code)

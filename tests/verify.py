"""Decode generated geometry independently and compare with vanilla source assets."""
from pathlib import Path
import hashlib
import json
import math
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
assets = Path(sys.argv[1])
out = ROOT/'generated'
manifest = json.loads((out/'manifest.json').read_text())


def u32(b, p):
    return struct.unpack_from('<I', b, p)[0]


def digest(b):
    return hashlib.sha256(b).hexdigest()


def legacy_vertices(b, p, end):
    vertices = []
    mappings = {}
    while p < end:
        start = p
        size = struct.unpack_from('<H', b, p)[0]*16
        p += 16
        while u32(b, p) != 0x8000:
            cmd = u32(b, p)
            if cmd == 0:
                assert u32(b, p+12) == 0
                mappings[u32(b, p+8)] = u32(b, p+4)
                p += 128
            elif cmd == 1:
                n = u32(b, p+8)
                p += 32
                for _ in range(n):
                    raw = b[p:p+48]
                    values = struct.unpack('<3fI7fI', raw)
                    assert all(math.isfinite(v) for v in values[:3]+values[4:11])
                    vertices.append((mappings[values[3]], raw))
                    p += 48
            else:
                raise AssertionError(f'Unknown legacy command: {cmd}')
        p += 32
        assert p == start+size+16
    assert p == end
    return vertices


def hd_vertices(b, mesh):
    p = mesh+16
    mappings = {}
    vertices = []
    faces = []
    while u32(b, p) != 0x8000:
        cmd, size = struct.unpack_from('<II', b, p)
        assert size > 0 and p+size <= len(b)
        if cmd == 17:
            mappings[u32(b, p+12)] = u32(b, p+8)
        elif cmd == 1:
            count = u32(b, p+8)
            for i in range(count):
                raw = b[p+32+i*48:p+32+(i+1)*48]
                slot, face, ignore = struct.unpack_from('<HBB', raw, 12)
                assert mappings[slot] >= 0
                vertices.append((mappings[slot], raw))
                if not ignore and face in (1, 2):
                    assert len(vertices) >= 3
                    faces.append((len(vertices)-3, len(vertices)-2, len(vertices)-1))
        else:
            raise AssertionError(f'Unknown HD command: {cmd}')
        p += size
    return vertices, faces


for name, expected in manifest['payload'].items():
    assert digest((out/name).read_bytes()) == expected, name
    assert name == 'xw_ex_5010.wpn' or name.startswith(('remastered/xw_ex_5010.wpn/', 'remastered/xw_ex_5010.se/'))
assert len(manifest['payload']) == 10
assert not any(n.endswith(('.dll', '.lua', '.bin', '.mset', '.se')) for n in manifest['payload'])

vanilla = (assets/'xw_ex_5010.wpn').read_bytes()
boss = (assets/'xa_ex_1580.mdls').read_bytes()
wpn = (out/'xw_ex_5010.wpn').read_bytes()
menv = u32(wpn, 8)
assert menv == u32(vanilla, 8)
assert wpn[menv:menv+4] == b'MENV'
mh = struct.unpack_from('<16I', wpn, menv)
assert u32(wpn, 12) == (len(wpn)+255)&~255
model = menv+mh[8]
assert struct.unpack_from('<4I', wpn, model) == (13, 0xffffffff, 0xffffffff, 1)
assert struct.unpack_from('<4I', wpn, model+16) == (0, 0, 0, 64)
vertices = legacy_vertices(wpn, model+64, menv+mh[10])
assert len(vertices) == 610 and {bone for bone, _ in vertices} == {2}
source_model = 128+u32(boss, 128+32)
source_start = source_model+u32(boss, source_model+16+7*16+12)
source_end = source_model+u32(boss, source_model+16+8*16+12)
source_vertices = legacy_vertices(boss, source_start, source_end)
assert {bone for bone, _ in source_vertices} == {211}
assert [raw for _, raw in vertices] == [raw for _, raw in source_vertices]
assert menv+mh[4]+mh[5] == menv+mh[6]
assert menv+mh[6]+mh[7] == menv+mh[1]+8
footer = vanilla[u32(vanilla, 8)+u32(vanilla, u32(vanilla, 8)+4)+8:]
footer_start = menv+mh[1]+8
assert wpn[footer_start:footer_start+len(footer)] == footer
assert wpn[menv+mh[4]:menv+mh[4]+16384] == boss[0x80680:0x84680]
assert wpn[menv+mh[6]:menv+mh[6]+1024] == boss[0x85e80:0x86280]

# Independently checked source addresses: nine pppColor keys, particles 3-5.
# Retain timestamps and later signed alpha deltas; use Riku's initial opacity.
expected_effects = bytearray(vanilla[128:menv])
rgb_offsets = []
for start in (0x3b40, 0x3d7c, 0x3fac):
    for i in range(3):
        key = start + i*12
        assert wpn[key:key+4] == vanilla[key:key+4]
        alpha = boss[0x86baa:0x86bac] if i == 0 else vanilla[key+10:key+12]
        assert wpn[key+10:key+12] == alpha
        if i == 0:
            assert struct.unpack('<h', alpha)[0] == 16384
        expected_effects[key+10-128:key+12-128] = alpha
        source_key = 0x86ba0+i*12
        rgb = boss[source_key+4:source_key+10]
        assert struct.unpack('<3h', rgb) == ((16384,)*3 if i == 0 else (0,)*3)
        assert wpn[key+4:key+10] == rgb
        expected_effects[key+4-128:key+10-128] = rgb
        rgb_offsets.append(key+4)
expected_effects[0x4310-128:0x6710-128] = boss[0x86c60:0x89060]
# OMD mesh 4 Alpha uses native Riku mesh 0 blending equation.
assert vanilla[0x8566] == 0x48 and boss[0x89086] == 0x44
assert wpn[0x8566] == 0x44
assert manifest['trail_blend'] == {'offset': 0x8566, 'original': 0x48, 'replacement': 0x44}
expected_effects[0x8566-128] = boss[0x89086]
# GS ALPHA bitfields: (A-B)*C+D. Source=0, destination=1, zero=2.
decode = lambda byte: tuple((byte >> (2*i)) & 3 for i in range(4))
assert decode(vanilla[0x8566]) == (0, 2, 0, 1)  # Cs*As + Cd: additive
assert decode(wpn[0x8566]) == (0, 1, 0, 1)  # (Cs-Cd)*As + Cd: alpha
assert wpn[128:menv] == expected_effects
assert manifest['trail_rgb_offsets'] == rgb_offsets
assert len(wpn) == footer_start+len(footer)
assert (out/'remastered/xw_ex_5010.wpn/-4310.dds').read_bytes() == (assets/'remastered/xa_ex_1580.mdls/-86c60.dds').read_bytes()

hd = (out/'remastered/xw_ex_5010.wpn/-9a40.cvbl').read_bytes()
assert struct.unpack_from('<IIHHI', hd) == (0x4126, 1, 0, 0, 0)
assert struct.unpack_from('<HHiiI', hd, 16)[1:] == (8, 0, 0, 32)
hdv, faces = hd_vertices(hd, 32)
source_hd = (assets/'remastered/xa_ex_1580.mdls/-c0.cvbl').read_bytes()
source_mesh = u32(source_hd, 16+2*32+12)
source_hdv, source_faces = hd_vertices(source_hd, source_mesh)
assert len(hdv) == 426 and len(faces) > 100
assert {bone for bone, _ in hdv} == {2}
assert {bone for bone, _ in source_hdv} == {211}
assert [raw for _, raw in hdv] == [raw for _, raw in source_hdv]
assert faces == source_faces
# Both legacy and HD material aliases must resolve to the same actual blade texture.
for n in ['-9b40.dds', '-%x.dds'%(menv+mh[4])]:
    assert (out/'remastered/xw_ex_5010.wpn'/n).read_bytes() == (assets/'remastered/xa_ex_1580.mdls/-1c6.dds').read_bytes()

# Confirm each replacement sample occurs in actual sword attack events.
mset = (assets/'xa_ex_1580.mset').read_bytes()
dictionary = u32(mset, 8)
found = set()
for animation in [200, 201, 213, 214]:
    index = u32(mset, dictionary+animation*4)&0xfff
    start = 0xc0+u32(mset, 0xc0+index*4)
    end = 0xc0+u32(mset, 0xc0+(index+1)*4)
    for p in range(start, end-16, 4):
        frame, command, kind, reserve = struct.unpack_from('<fIII', mset, p)
        if 0 <= frame <= 200 and command&0xffff == 0 and kind == 2 and reserve == 0:
            if command>>16 in (8245, 8246, 8247, 8248):
                found.add(command>>16)
assert found == {8245, 8246, 8247, 8248}
for target, source in manifest['swing_map'].items():
    replacement = (out/f'remastered/xw_ex_5010.se/se{int(target):06d}.win32.scd').read_bytes()
    assert replacement == (assets/f'remastered/xa_ex_1580.mdls/se{source:06d}.win32.scd').read_bytes()
    assert replacement[:8] == b'SEDBSSCF'

print('PASS: 610 legacy vertices, 426 HD vertices, original UVs/faces, Sora bone binding,')
print('      fixed layout, Riku texture/tint/opacity and alpha blend, retained fade keys/times,')
print('      five swing slots traced to Riku attack events, payload hashes and scope.')
print('PENDING: live grip/orientation, trail visuals, audible timing, throws and world variants.')

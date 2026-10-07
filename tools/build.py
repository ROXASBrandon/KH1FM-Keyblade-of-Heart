"""Build the standalone KH1FM replacement from a local Steam asset extraction.

Usage: python tools/build.py PATH_TO_EXTRACTED_KH1
No game installation or save files are modified. Python standard library only.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.5-preview'
BOSS = 'xa_ex_1580.mdls'
WEAPON = 'xw_ex_5010.wpn'
# Verified in Riku's attack animation events: 200, 201, 213, and 214.
SWING_MAP = {11018: 8245, 11019: 8246, 11020: 8247, 11021: 8248, 11022: 8245}


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def put32(data, offset, value):
    struct.pack_into('<I', data, offset, value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def legacy_mesh(data, model_offset, mesh_index, section_end):
    mesh_count = u32(data, model_offset + 12)
    require(mesh_index < mesh_count, 'Missing requested legacy mesh')
    table = model_offset + 16
    start = model_offset + u32(data, table + mesh_index * 16 + 12)
    end = (model_offset + u32(data, table + (mesh_index + 1) * 16 + 12)
           if mesh_index + 1 < mesh_count else section_end)
    return bytearray(data[start:end])


def remap_legacy(packet):
    """Remap the blade's sole Riku joint 211 to Sora weapon joint 2."""
    p = 0
    vertices = 0
    definitions = 0
    while p < len(packet):
        require(p + 16 <= len(packet), 'Truncated legacy packet')
        qwc = struct.unpack_from('<H', packet, p)[0]
        start = p
        p += 16
        while True:
            require(p + 16 <= len(packet), 'Truncated legacy command')
            command = u32(packet, p)
            if command == 0x8000:
                p += 32
                break
            if command == 0:
                require(u32(packet, p + 4) == 211 and u32(packet, p + 12) == 0,
                        'Blade joint/weight layout differs from supported extraction')
                put32(packet, p + 4, 2)
                definitions += 1
                p += 128
            elif command == 1:
                count = u32(packet, p + 8)
                require(count <= 4096, 'Unreasonable legacy strip size')
                vertices += count
                p += 32 + 48 * count
            else:
                raise ValueError(f'Unsupported legacy packet command {command}')
        require(p == start + 16 + qwc * 16, 'Legacy DMA packet length mismatch')
    require(definitions == 1 and vertices == 610, 'Unexpected blade geometry')
    return packet


def hd_blade(data):
    """Extract the single rigid HD blade mesh, preserving face and UV data."""
    require(u32(data, 0) == 0x4126, 'Unexpected CVBL header')
    count = u32(data, 4)
    unknown_count, has_unknown = struct.unpack_from('<HH', data, 8)
    require(count == 6 and has_unknown == 1, 'Unexpected Riku HD mesh layout')
    table = 16 + unknown_count * 32
    flags, style, material, other, start = struct.unpack_from('<HHiiI', data, table)
    end = u32(data, table + 16 + 12)
    require(style == 8 and material == 0, 'Unexpected blade HD vertex format')
    packet = bytearray(data[start:end])
    p = 16
    definitions = 0
    vertices = 0
    while p < len(packet):
        command, size = struct.unpack_from('<II', packet, p)
        if command == 0x8000:
            require(p + 16 <= len(packet), 'Truncated CVBL end marker')
            p += 16
            break
        require(size >= 16 and size % 16 == 0 and p + size <= len(packet),
                'Invalid CVBL subsection bounds')
        if command == 17:
            require(u32(packet, p + 8) == 211, 'HD blade attached to unexpected joint')
            put32(packet, p + 8, 2)
            definitions += 1
        elif command == 1:
            vertices += u32(packet, p + 8)
        else:
            raise ValueError(f'Unsupported CVBL subsection {command}')
        p += size
    require(definitions == 1 and vertices == 426, 'Unexpected HD blade geometry')
    # Single-mesh WPN CVBL header; discard Riku's body and character-only entries.
    header = struct.pack('<IIHHI', 0x4126, 1, 0, 0, 0)
    mesh = struct.pack('<HHiiI', flags, 8, 0, 0, 32)
    return header + mesh + packet


def dpd_texture(data, dpx_base):
    """Return texture 0 of DPD 0, including PS2 pixels and CLUT."""
    effects = u32(data, dpx_base + 12)
    dpd_table = dpx_base + 16 + effects * 32
    require(u32(data, dpd_table) > 0, 'Missing effect data')
    dpd = dpx_base + u32(data, dpd_table + 4)
    require(u32(data, dpd) == 0x96, 'Unexpected DPD format')
    particle_count = u32(data, dpd + 4)
    texture_table = dpd + 8 + particle_count * 4
    require(u32(data, texture_table) == 1, 'Expected one trail texture')
    texture = dpd + u32(data, texture_table + 4)
    width, height = struct.unpack_from('<HH', data, texture + 12)
    require((width, height) == (128, 64), 'Trail dimensions changed')
    require(struct.unpack_from('<H', data, texture + 6)[0] == 0x13,
            'Expected 8-bit indexed trail')
    return texture, data[texture + 32:texture + 32 + width * height + 1024]


def color_keys(data, particle):
    """Decode program 43 (pppColor) keys; offsets are relative to PData."""
    base = particle + 272
    table = base + u32(data, base + 12)
    count = u32(data, table)
    require(0 < count <= 32, 'Unexpected particle entry count')
    keys = []
    for i in range(count):
        entry = base + u32(data, table + 4 + i*4)
        programs = struct.unpack_from('<H', data, entry + 38)[0]
        require(0 < programs <= 32, 'Unexpected program count')
        for j in range(programs):
            kind, size, number, params, useval = struct.unpack_from(
                '<IHHII', data, entry + 40 + j*16)
            if kind == 43:
                require(size == 12 and 0 < number <= 32, 'Unexpected color key layout')
                for k in range(number):
                    key = base + params + k*size
                    require(key + size <= len(data), 'Color key outside resource')
                    keys.append(key)
    return keys


def dpd_model(data, dpd, index):
    table = dpd + 4
    for _ in range(3):  # particle, texture, shape offset tables
        table += 4 + u32(data, table)*4
    require(index < u32(data, table), 'Missing trail model')
    return dpd + u32(data, table + 4 + index*4)


def adapt_trail_colors(wpn, boss):
    """Adapt RGB keys and initial opacity; retain later alpha keys and timing."""
    out = bytearray(wpn)
    base = u32(out, 4)
    table = base + 16 + u32(out, base + 12)*32
    require(u32(out, table) == 1, 'Expected original single DPD')
    dpd = base + u32(out, table + 4)
    require(u32(out, dpd + 4) == 7, 'Unexpected Sora particle count')
    boss_base = u32(boss, 8)
    boss_table = boss_base + 16 + u32(boss, boss_base + 12)*32
    boss_dpd = boss_base + u32(boss, boss_table + 4)
    source_keys = color_keys(boss, boss_dpd + u32(boss, boss_dpd + 8))
    require(len(source_keys) == 3, 'Unexpected Riku trail colors')
    source_rgb = [boss[p+4:p+10] for p in source_keys]
    require(source_rgb == [struct.pack('<3h', 16384, 16384, 16384), bytes(6), bytes(6)],
            'Riku neutral trail tint differs from supported source')
    source_alpha = boss[source_keys[0]+10:source_keys[0]+12]
    require(struct.unpack('<h', source_alpha)[0] == 16384,
            'Unexpected Riku starting trail opacity')
    # Existing trail models share the same packet layout. Adapt only OMD Alpha.
    target_model = dpd_model(out, dpd, 4)
    source_model = dpd_model(boss, boss_dpd, 0)
    require(out[target_model+16:target_model+38] == boss[source_model+16:source_model+38],
            'Trail model primitive/vector layout differs')
    require(out[target_model+38] == 0x48 and boss[source_model+38] == 0x44,
            'Unexpected original trail blending fields')
    out[target_model+38] = boss[source_model+38]
    edited = []
    for index in (3, 4, 5):
        particle = dpd + u32(out, dpd + 8 + index*4)
        keys = color_keys(out, particle)
        require(len(keys) == 3 and u32(out, keys[0]) == 0,
                'Unexpected Sora trail color envelope')
        for key, rgb in zip(keys, source_rgb):
            out[key+4:key+10] = rgb
            edited.append(key+4)
        # Use native Riku starting opacity with alpha blending.
        # Keep Sora's later signed alpha deltas and all key times unchanged.
        out[keys[0]+10:keys[0]+12] = source_alpha
    texture, _ = dpd_texture(out, base)
    _, pixels = dpd_texture(boss, boss_base)
    out[texture+32:texture+32+len(pixels)] = pixels
    return bytes(out), edited


def weapon_file(vanilla, boss):
    require(u32(vanilla, 0) == 2 and boss[128:132] == b'MOBJ', 'Invalid input headers')
    menv = u32(vanilla, 8)
    require(vanilla[menv:menv+4] == b'MENV', 'Missing Kingdom Key model environment')
    bh = struct.unpack_from('<16I', boss, 128)
    model = 128 + bh[8]
    require(u32(boss, model) == 247 and u32(boss, model + 12) == 9,
            'Unsupported Riku model')
    source_info = 128 + bh[2] + 6 * 16
    texture_info = boss[source_info:source_info + 16]
    # Seven equal 128x128 character textures; Keyblade of Heart is texture 6.
    require(bh[5] == 7 * 128 * 128 and bh[7] == 7 * 1024,
            'Riku texture bank dimensions changed')
    pixels = boss[128 + bh[4] + 6 * 16384:128 + bh[4] + 7 * 16384]
    palette = boss[128 + bh[6] + 6 * 1024:128 + bh[6] + 7 * 1024]
    packet = remap_legacy(legacy_mesh(boss, model, 7, 128 + bh[10]))
    body = bytearray(vanilla[menv+64:menv+128]) + packet
    # Header describes 13 Sora weapon joints and one material-0 mesh.
    require(u32(body, 0) == 13 and u32(body, 12) == 1, 'Unexpected Kingdom Key rig')
    tex_info_offset = 64 + len(body)
    tex_data_offset = (tex_info_offset + 16 + 127) & ~127
    clut_offset = tex_data_offset + len(pixels)
    model_env = bytearray(clut_offset + len(palette))
    mh = list(struct.unpack_from('<16I', vanilla, menv))
    mh[1] = len(model_env) - 8
    mh[2:8] = [tex_info_offset, 16, tex_data_offset, len(pixels), clut_offset, len(palette)]
    mh[9:14] = [len(body), tex_info_offset, 0, tex_info_offset + 16, 0]
    struct.pack_into('<16I', model_env, 0, *mh)
    model_env[64:64+len(body)] = body
    model_env[tex_info_offset:tex_info_offset+16] = texture_info
    model_env[tex_data_offset:tex_data_offset+len(pixels)] = pixels
    model_env[clut_offset:] = palette
    old_end = menv + u32(vanilla, menv+4) + 8
    require(vanilla[old_end:old_end+4] == b'TEXA', 'Missing vanilla TEXA footer')
    out = bytearray(vanilla[:menv]) + model_env + vanilla[old_end:]
    put32(out, 12, (len(out) + 255) & ~255)
    # Recovery keeps the original effect bank, registration order, and trail.
    old_texture, _ = dpd_texture(vanilla, u32(vanilla, 4))
    return bytes(out), menv + tex_data_offset, old_texture + 32


def build(assets):
    expected = json.loads((ROOT/'tools/input-hashes.json').read_text())
    inputs = {}
    for name, digest in expected.items():
        inputs[name] = (assets/name).read_bytes()
        require(sha(inputs[name]) == digest, f'Unsupported/modified source: {name}')
    vanilla = inputs[WEAPON]
    boss = inputs[BOSS]
    wpn, legacy_texture_offset, trail_offset = weapon_file(vanilla, boss)
    wpn, color_offsets = adapt_trail_colors(wpn, boss)
    cvbl = hd_blade(inputs[f'remastered/{BOSS}/-c0.cvbl'])
    payload = {WEAPON: wpn,
        f'remastered/{WEAPON}/-9a40.cvbl': cvbl,
        f'remastered/{WEAPON}/-9b40.dds': inputs[f'remastered/{BOSS}/-1c6.dds'],
        f'remastered/{WEAPON}/-{legacy_texture_offset:x}.dds': inputs[f'remastered/{BOSS}/-80680.dds'],
        f'remastered/{WEAPON}/-{trail_offset:x}.dds': inputs[f'remastered/{BOSS}/-86c60.dds']}
    for target, source in SWING_MAP.items():
        payload[f'remastered/xw_ex_5010.se/se{target:06d}.win32.scd'] = inputs[
            f'remastered/{BOSS}/se{source:06d}.win32.scd']
    out = ROOT/'generated'
    out.mkdir(exist_ok=True)
    # Remove only stale generated payload from this builder's previous manifest.
    old_manifest = out/'manifest.json'
    if old_manifest.exists():
        for name in json.loads(old_manifest.read_text())['payload']:
            path = out/name
            require(path.resolve().is_relative_to(out.resolve()), 'Unsafe prior manifest path')
            if name not in payload and path.is_file():
                path.unlink()
    for name, data in payload.items():
        path = out/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {'version': VERSION, 'source_model': BOSS, 'source_mesh': 7,
                'legacy_vertices': 610, 'hd_vertices': 426,
                'bone_remap': {'211': 2}, 'swing_map': SWING_MAP,
                'trail': 'Riku texture, neutral tint, initial opacity and alpha blend in Sora trail',
                'trail_blend': {'offset': 0x8566, 'original': 0x48, 'replacement': 0x44},
                'trail_rgb_offsets': color_offsets,
                'hit_audio': 'vanilla material-specific hit sounds retained',
                'inputs': expected,
                'payload': {name: sha(data) for name, data in payload.items()}}
    old_manifest.write_text(json.dumps(manifest, indent=2)+'\n')
    metadata = ['title: Keyblade of Heart over Kingdom Key',
                'originalAuthor: ROXASBrandon', 'game: kh1',
                'description: >',
                '  Replaces Kingdom Key with Riku\'s Keyblade of Heart mesh and textures,',
                '  and five swing sound slots. Stats and Sora animations stay unchanged.',
                '  Steam preview 0.1.5; Riku blue texture, neutral tint, opacity and alpha blending.',
                '  Dark-gradient appearance needs a live check.', 'assets:']
    for name in payload:
        metadata += [f'  - name: {name}', '    method: copy',
                     '    source:', f'      - name: generated/{name}']
    (ROOT/'mod.yml').write_text('\n'.join(metadata)+'\n')
    return manifest


def package():
    manifest = json.loads((ROOT/'generated/manifest.json').read_text())
    files = ['mod.yml', 'README.md', 'CREDITS.md', 'DEVELOPMENT.md', 'images/model-preview.png', 'images/banner.gif', 'images/banner.png',
             'tools/build.py', 'tools/input-hashes.json', 'tests/verify.py',
             'generated/manifest.json'] + ['generated/'+n for n in manifest['payload']]
    dist = ROOT/'dist'
    dist.mkdir(exist_ok=True)
    archive = dist/f'Keyblade-of-Heart-v{VERSION}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in files:
            data = (ROOT/name).read_bytes()
            for marker in [b'/' + b'mnt/', b'Users' + b'/', b'Users' + b'\\']:
                require(marker not in data, f'Private identifier in {name}')
            require(re.search(rb'\b76[0-9]{15}\b', data) is None, f'Steam account ID in {name}')
            info = zipfile.ZipInfo(name, (2026, 10, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None and set(z.namelist()) == set(files), 'ZIP integrity failure')
    (dist/'SHA256SUMS.txt').write_text(sha(archive.read_bytes())+'  '+archive.name+'\n')
    print(archive)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assets', type=Path)
    args = parser.parse_args()
    build(args.assets)
    # Independent decoder and source comparisons, then archive only on success.
    import subprocess
    import sys
    subprocess.run([sys.executable, str(ROOT/'tests/verify.py'), str(args.assets)], check=True)
    package()

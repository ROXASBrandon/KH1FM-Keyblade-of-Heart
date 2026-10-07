# Development evidence

## Model

The supported `xa_ex_1580.mdls` has 9 legacy meshes. Mesh 7 contains the blade,
material 6, and 610 strip vertices; every vertex uses Riku joint 211. Its HD
`-c0.cvbl` has 6 meshes. HD mesh 0 is the same blade, with 426 vertices and a
single joint-211 mapping. The texture is the character's material-6 image;
HD material slot 0 resolves through `-1c6.dds`.

Conversion preserves every position, normal, UV, and face marker. Only the joint
mapping changes from 211 to Sora weapon joint 2. The 13-joint Kingdom Key weapon
header and model prefix remain compatible with Sora's existing skeleton. Other
Riku body meshes, animation data, and character-only CVBL records are excluded.

The builder retains Kingdom Key's effects prefix and TEXA footer, rebuilds the
MENV section with one texture, and emits aliases for HD and legacy texture
addresses. Both aliases use the authentic blade texture. There is no keychain
mesh because the original Keyblade of Heart has none.

## Trail

Riku DPD 0 has one 128x64 indexed texture at the source's `0x86c60` pixel offset.
Kingdom Key's DPD 0 texture at `0x4310` has matching dimensions, format, and
VRAM header. Preview 0.1.0 replaced indexed pixels and palette plus the matching remastered
DDS. The model rendered in gameplay, but the user saw no trail. Read-only
inspection confirmed the replacement texture was present in the live weapon.

Preview 0.1.1 appended the first Riku DPD bank and routed effects 0 and 2 to it,
with Sora attachment fields. It passed structural checks but failed gameplay:
the blade sampled Sora body textures, menu rendering was corrupted, and no
trail appeared. Adding a DPD likely changed HD resource registration order;
this cause is an inference, not a fully traced loader result.

Recovery 0.1.2 removes that bank and all reroutes. It preserves the complete
original effects prefix byte-for-byte, including original indexed pixels and
palette, and uses the original Kingdom Key trail DDS. Model and audio data
remain unchanged from the working initial preview. This is a recovery and
trail baseline, not a completed custom blue-trail implementation.

## Audio

Riku's `xa_ex_1580.mset` animation dictionary is its second section. The MMTN
motion offsets are relative to its offset list at `0xC0`, not section `0x80`.

Decoded attack-event records:

| Animation | Frame | Sound ID |
| --- | --- | --- |
| 200 | 10 | 8245 |
| 200 | 38 | 8246 |
| 201 | 10 | 8246 |
| 201 | 38 | 8245 |
| 213 | 6 | 8247 |
| 213 | 14 | 8246 |
| 213 | 40 | 8245 |
| 214 | 8 | 8248 |

These kind-2 audio events reference the matching SCD recordings bundled with
Riku's model. Kind-1 companion records reference voice IDs and are excluded.
The replacement uses 8245 for Kingdom Key slots 11018 and 11022, 8246 for 11019,
8247 for 11020, and 8248 for 11021. Slots are mapped to Sora's existing timing;
these recordings are not guessed from a sound directory listing.

Sound IDs 8219 onward also appear in Riku's particle data, including charge and
special effects. Those are not substituted for ordinary weapon impacts. The
base weapon sound bank and its 35 material-specific hit variants remain intact.

## Validation boundary

`tests/verify.py` independently decodes both generated mesh formats and compares
all vertex data against the source. It checks bindings, faces, texture ranges,
TEXA, byte-identical original effect bank and DDS, actual MSET sound-event references, and the
payload allowlist. The archive builder verifies ZIP contents, hashes, and known
private path/identifier markers.

Passing these checks does not prove live engine rendering, sound installation,
Strike Raid behavior, or stability. Test in a normal, Halloween Town, and
Atlantica session; check room transitions and removal. No unsupported loader
hooks are introduced. Changes remain within the OpenKH resource overlay.

## Live audio and Transmog diagnosis

Read-only inspection of the live current-weapon sound registry found all five
replacement samples. Each SCD matched the original Riku recording except one
runtime-mutated byte at offset 0x12F. The user still found the sound subtle.
The package retains original sample volume and ordinary material hit sounds.

The installed experimental Transmog helper (diagnostic build 2007) reached
failed preload phase 4 with no ready weapons or appearance swaps. Its source
validates vanilla file sizes and WPN/MENV headers; the custom Kingdom Key fails
that check. Its fallback leaves the independently loaded replacement visible.
The standalone package does not change those helper guards.

## Version 0.1.3 trail tint adaptation

The recovery gameplay screenshot confirms a visible orange swing trail and the
correct blade. DPD particles 3, 4, and 5 map to DPX effect IDs 5, 6, and 7. Each
has one program 43 (pppColor) with three 12-byte parameter keys. A key contains
a 32-bit time followed by four signed 16-bit RGBA components. Their first RGB
values strongly favor red; the earlier texture-only swap retained this tint.

Riku DPD 0 uses the same color program and a neutral initial RGB of
(16384, 16384, 16384), followed by two zero RGB deltas. Version 0.1.3 copies
only those three RGB triplets into each existing Sora trail envelope. The nine
keys are at 0x3B40, 0x3D7C, and 0x3FAC with 12-byte stride. All Sora timestamps
and alpha values remain intact. Only 54 RGB bytes change, plus original-sized
indexed texture/palette data and its existing remastered DDS alias. Nothing is
appended and neither HD registration order nor model addresses change.

The program layout is independently decoded against OpenKH DpdPData.cs and the
KH1 decompilation PColor/pppHCVECTOR definitions. This establishes the parameter
meaning, not the final engine appearance. The RGB filtering explanation is an
inference pending live validation. Do not label the blue trail confirmed yet.

## Version 0.1.4 native starting opacity

The user confirmed the blue trail works in 0.1.3. Their boss-fight reference
shows a stronger black-to-blue gradient. Both versions already use the authentic
Riku DDS, indexed pixels, palette, and neutral RGB tint. Riku’s first color key
starts alpha at 16384; Sora’s corresponding keys start at 12032 or 12152.
The color program releases these as output alpha bytes 128 versus 94.

Version 0.1.4 copies only Riku’s first alpha short into each of the three Sora
trail envelopes. All later signed alpha values, key timestamps, attachment,
geometry, texture files, and model addresses remain unchanged. Higher starting
opacity is intended to reveal more of the original texture’s dark regions;
that effect is an inference pending a gameplay comparison. This retains Sora’s
later fade parameters, not an identical normalized opacity curve.

Read-only executable inspection also traced the trail’s program 200 to
pppRyjDrawShipolyBone. Parameter byte +0x0E is shifted by five and passed as a
memory allocation size for trail history; the 8-versus-7 difference is not a
blend-mode selector. The compared texture headers match byte-for-byte. No
speculative rendering-mode patch is included.

## Version 0.1.5 native trail blending

Version 0.1.4 did not produce the dark edge in the gameplay screenshot. The
trail uses program 200 (pppRyjDrawShipolyBone), with Sora DPD model 4. Riku uses
the same draw program with its DPD model 0. Both effect-model packets have size
0x1070 and matching OMD primitive/vector layout fields. Their OMD Alpha bytes
differ: Sora 0x48 at weapon offset 0x8566; Riku 0x44 at source offset 0x89086.
OpenKH DpdModel.cs identifies the OMD Alpha/AlphaFix fields.

The GS blend register encodes A/B/C/D as four two-bit selectors in the formula
(A-B)*C+D. 0x48 decodes to (source-zero)*sourceAlpha+destination, which adds light.
0x44 decodes to (source-destination)*sourceAlpha+destination, which permits dark
texture regions to occlude the background. This explains why stronger opacity
alone could not generate a black rim under the additive equation.

The builder independently walks both DPD model tables, verifies the compatible
layout and expected Alpha bytes, then copies only the native Riku Alpha byte.
No DPD banks, primitive packets, counts, textures, or resources are appended.
Tests independently check the addresses, source byte, selector math, and full
effect-prefix allowlist. The final appearance still needs a gameplay check.

## Build from source

Use Python 3.9+ and a vanilla Steam KH1 extraction with its remastered folder:

```text
python tools/build.py PATH_TO_EXTRACTED_KH1
```

The builder verifies source fingerprints, runs independent asset checks, and creates the ZIP under dist.

The author confirmed the dark-gradient appearance and clean OpenKH builds of this mod alone, Transmog alone, and both together.

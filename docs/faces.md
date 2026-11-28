# Missing faces and skin

Status 2026-11-28: **not fixed.** In most runs the close-up characters of the game's opening
scene show eyes, teeth and hair, but no head mesh; distant characters have complete heads. In
about one run in six the same build draws the faces correctly. Every claim below is marked
**[obs]** (seen in a run, log or capture), **[src]** (source or commit) or **[inf]** (inference).

## How to check a build

1. Run the opening scene (the prison cutscene, then the "PROLOGUE" card) from a copy of the
   save, so that the scene is the same every time. `VICTORY_DATA_DIR` points the game at a copy
   of `%LOCALAPPDATA%\victory\data`; your own save is never touched.
2. Hold scripted presses long enough: `VICTORY_KEYS=25-26:Enter,40-41:Enter,…`. A 0.2 s press
   (`25:Enter`) is missed at 4 fps, where the title polls input about every 250 ms. [obs]
3. Take window shots every 5 s from 115 to 210 s (`xdotool search --name Americas` +
   `import -window`). 120–125 s shows a face front-on in the night-street flashback. [obs]
4. Judge the depth target, not only colour: a diagnostics build with
   `VICTORY_CAPTURE=<dir> VICTORY_CAPTURE_WORLD=1 VICTORY_CAPTURE_WORLD_ALSO=150,250
   VICTORY_DUMP_TARGETS=1` writes `frame-N-*-853x480-f19.png`; a missing head is a hole between
   hair and collar with the eyes floating in it. [obs]
5. Repeat a run at least three times before calling a result: one good run proves nothing
   (see "The race"). Keep the game under a RAM cap (`systemd-run --user --scope
   -p MemoryMax=20G`) and never copy large files into `/tmp` (tmpfs, i.e. RAM).

## What draws a character [obs, frame ops capture]

`ped_skinning_cs:CS_PedStandardSkinning_N` (compute, pre-skins vertices into buffers) →
`meshlet_culling:CS_BatchCullPreSkinnedMeshlets` (compute, writes indirect arguments) →
indirect mesh dispatch `MS_VS_PedTransformPreSkinD` + `PS_DeferredPedTexturedNC` into a
six-target G-buffer (853x480). Eyes go through `MSNC_VS_TransformSkinD`; hair is strands
(`CS_VisHair`, `VS_HairStrandGeometry`). None of these draws is dropped: the frame's
`DROPPED` lines name only the skipped `PS_Octa*` imposters. All eleven ped-skinning computes
translate directly for a 32-lane host (no wave64 retry form).

Names come from `victory shader-info -v shaders.rpf`, which prints every container's name
and hash. **The four hashes in `card_steps.rs:16-25` that earlier notes called "skinning
shaders" (17af15c4…, 55679a05…, 89d08e56…, fdd56d97…) are instance culling for props and
trees** (`InstanceCulling:CS_Prop_BatchCull` etc.); the skip-listed `VICTORY_SKIP_PIPELINES`
hashes are `PS_Octa*` imposters.

## The race [obs]

The 11-26 15:43 diagnostics binary from the old worktree (pre-`fe7483606`), run with the
original `run-gtavi.sh` environment, drew complete faces once (10–12 fps through the
cutscene). Four more runs of the same binary and launch did not. Today's build failed in every
run tried. The good run's warnings were the same as a bad run's; frame rate is not the
deciding factor (later bad runs of the old binary also reached 12–15 fps).

## Ruled out (one change each, opening scene)

- Write-to-read ordering between the title's commands: `VICTORY_BARRIER_ALL=1` (a
  diagnostic switch: a full UAV barrier before every draw, dispatch and indirect execution)
  leaves the faces missing (3.3-3.9 fps, 260 s run). [obs]
- Stale decoration-bake sources: the cloud branch's bake-source refresh (8f95922) runs
  (1,585 sources over 128 bakes, 25 % refreshed, 0 failed) and faces stay missing. [obs]
- Wave32 lowering of the four culling shaders: the "wave-wide branches" form in their place
  changes nothing. [obs]
- `VICTORY_SKIP_PIPELINES` empty; `VICTORY_THIN=0`; `VICTORY_STREAM_NOTICE=0`. [obs]
- Every tuning switch off (the layer's defaults): still missing. [obs]
- The old `run-gtavi.sh` settings (`CARD_BUFFERS_MB=1024`, `LEVELS_ON_DEMAND=0`, …) on
  today's build. [obs]
- System changes: no Wine, driver, Mesa or Vulkan update since 2026-11-20; vkd3d-proton and
  DXVK DLLs in the prefix unchanged since 2026-11-26 11:18. [obs]
- DirectStorage ordering: `VICTORY_DSTORAGE_TRACE=1` over a full run shows 2,826 batches,
  none started early, no page list unfilled, no mapping change during a request. [obs]
- Upstream `29210929d` (page writes reported where the pages show when the write ends) is
  already in this tree in another form (`xmem.rs` `pages_written`, test
  `page_windows_report_where_the_pages_show_when_done`). [src]
- Group barriers: `VICTORY_GROUP_SYNC` defaults to every barrier waiting, which is what
  vilocity `d1e08f5e2` enables for ped skinning and meshlet culling. [src]

## Leads

- flamingo `39bb53a11` (run 236): every mesh that comes out wrong reads a geometry buffer
  whose GPU copy does not hold decoded vertex streams, while title memory holds an encoded
  container (`42 40 00 00 26 …`); correct meshes have the decoded streams on the card. [src]
- PastiiJay `8b76aec38` / realhenners `af2aecf6c` (upstream VIctory #334): per-list state
  tracking of host copies between shader writes and reads; fixed meshlet-cull dispatches
  that lost character geometry on AMD. Not in this tree. [src]
- cool_fox README: "`VICTORY_THIN=1` … NVIDIA: head skin may not draw" (THIN=0 alone did
  not help here). [src]
- The game has a GPU morphable head model, `ped_morphable_model_cs` (`CS_MorphableInit`
  9cfa9aa6a5e60f26, `CS_MorphableShapeSolve` fe3678eae1ca9a1a, `CS_MorphableReconstructNormals`
  7ae4af7c4309b836). In no run (the good one included) is any of the three translated or bound.
  The title never asks for them through any creation method: they are not among the pipelines
  of the unimplemented slots either (next item). [obs]

## Refuted: the pipelines of device slots 0x1c8 and 0x218

The 29 warnings are not 29 pipelines: the warning is logged at powers of two. 2026-11-28, a
diagnostics run with `VICTORY_DUMP_UNKNOWN_PIPELINES=<dir>` (70 s, opening scene) recorded
1,609 calls of `0x1c8` and 37 of `0x218`, all during start-up. [obs]

- A `0x218` description is a compute pipeline made of the pieces an `FSPX` part holds: root
  signature, registers (0x1c B), a hash (0x14 B), GFX10 code in GPU memory, program metadata
  (0x403 ...). `0x1c8` is the graphics form: registers (0x184 B), 0xbc B, then two stages each
  as code and metadata. [obs]
- The code's first 16 bytes match exactly one program in `shaders.rpf` for every call
  (`shader-info -v` now prints each program's `head#`); the rest differs, as the title patches
  the code it uploads. [obs]
- They are the engine's utility shaders: the 37 computes are `im:CS_Texture2D*Mipmap*` and
  `im:CS_*Depth*Copy*`; the graphics ones are Scaleform UI (`FVertex*`, `FTexTG*` with
  `VVertex*`/`VTexTG*`, about 1,550), Bink video, blits, depth downsamples, a QR code. **No ped,
  head, skin, hair or morph shader is among them.** [obs]
- So lead "the morphable heads are among the dropped pipelines" is refuted. The dropped
  mipmap computes may matter for other textures; not followed here. [obs,inf]
- flamingo's conclusion "the decoding is on the card and the card copy is the one to keep"
  [src]: a GPU-only result like that is lost if anything refreshes the GPU copy from title
  memory afterwards; worth testing with refreshes of those buffers suppressed. [inf]

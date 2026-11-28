# Missing faces and skin

Status 2026-11-28: **fixed** (commit `c9bfaa7ee`, tag `faces-working-2026-11-28`). Before it,
in most runs the close-up characters of the game's opening scene showed eyes, teeth and hair,
but no head mesh; distant characters had complete heads. About one run in six drew the faces.
Every claim below is marked **[obs]** (seen in a run, log or capture), **[src]** (source or
commit) or **[inf]** (inference).

## The cause and the fix

- The head is built by `ped_standard_head_cs`. Its blend-shape mapping,
  `CS_ComputeBlendshapeMappings` (`61e54e90adccaf6a`), is dispatched once per head, at the first
  frame the character is set up (frame ~1,540-1,650 of the opening scene). [obs]
- A dispatch whose host pipeline is not made within the frame's wait budget
  (`pipeline/make.rs` `ready`) is dropped. In that window the log reads "dropped 285.3/frame
  without a host pipeline: being made", and the mapping is not in any later frame. [obs]
- The mapping was among the dropped dispatches, so the head was never built. When its
  pipeline happened to be ready in time (for example, loaded from the pipeline library a
  previous run saved), the faces were there: the race. [inf, from the fix below]
- Fix: a pipeline using a shader that `VICTORY_PIPELINE_MUST` names (default: this hash) is
  made on the spot at first use and never dropped. With it, the heads were whole (profile,
  nose, chin, neck) in the depth target in 3 runs of 3, and in colour in a release build;
  before it, 0 of 6 runs that day. The idea comes from Vice-Tay #372 (via ruff-lab
  `f9e00b4b2`), a 2 s wait for this pipeline. [obs]
- Other passes the title runs only once could have the same problem; `VICTORY_PIPELINE_MUST`
  takes a list. A full pipeline prebake (`victory warm`) avoids it for every pipeline. [inf]
- Found by comparing other forks: ruff-lab's newest commit names a "head blend-shape wait";
  the capture's op lists showed every other `ped_standard_head_cs` pass running and this one
  absent.

## What follows is the investigation before the fix

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
- Title buffer copies done on the CPU out of bytes a shader wrote (flamingo's "the card copy
  is the one to keep"): a copy into memory with no host copy is done at submission, in title
  memory, even where a shader wrote the source on the card (`host/place.rs`; the diagnostics
  build logs each one). This happens over 2,048 times in 150 s of the opening scene. Giving
  those destinations a host copy, so the copy runs on the card, left the heads missing in the
  depth target, and the driver ran out of memory (`NV_ERR_NO_MEMORY`) about 2 min in, in both
  runs; the change was not kept. 2026-11-28. [obs]
- Buffer state tracking, upstream VIctory #334 (PastiiJay `8b76aec38` / realhenners
  `af2aecf6c`; it fixed meshlet-cull dispatches that lost character geometry on AMD): ported
  on branch `faces-334` (`28589067d`) behind `VICTORY_BUFFER_STATES=1`. With it on the heads
  stay missing in the depth target (215 s, 5.4-5.5 fps). Not merged. 2026-11-28. [obs]
- The `cap_good` capture in `~/.cache/protonida-exp` also shows the heads missing in its depth
  targets: it was not taken during the one good run, so it is no reference. [obs]
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

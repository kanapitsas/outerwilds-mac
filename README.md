# Outer Wilds, native on Apple silicon

Builds a native arm64 macOS `Outer Wilds.app` from the Windows release of the
game. No Wine, no Rosetta: the game runs on Unity's own macOS arm64 player and
renders with Metal.

**You must own the game.** This repository contains no game files, no Unity
binaries and no decompiled game code. The app is built on your machine from
your own copy of the game (the Windows Steam release, 1.1.13) and Unity's
public installer, and is for your own use only: **do not share the built app**,
it contains the game's data and Unity's runtime.

The game still starts through Steam: the Steam client must be running and
signed in with an account that owns Outer Wilds.

> This is an unofficial fan project, not affiliated with or endorsed by Mobius
> Digital, Annapurna Interactive or Unity Technologies. Outer Wilds is a
> trademark of its owners. Provided as is, without warranty (see LICENSE).

## How it works

Outer Wilds is a Unity 2019.4.39f1 (Mono) game. Two things stand in the way of
simply dropping its data into a Mac player:

1. **Unity 2019.4 has no arm64 macOS player.** We use the 2020.3.48f1 arm64
   player instead. Player data has no type trees, so a 2020.3 player only reads
   files written with its own class layouts. `owport.upgrade` maps every object
   of the ~25 classes whose layout changed (Mesh, Texture2D, ParticleSystem,
   PlayerSettings, ...) from the 2019.4 tree onto the 2020.3 tree, using
   UnityPy's TPK type tree database; unchanged classes keep their bytes.
   `owport.runtime_json` writes the two JSON files 2020.3 reads instead of
   `MonoManager`/`RuntimeInitializeOnLoadManager` data.
2. **The Windows build only ships D3D11 shaders.** Unity compiled Metal shaders
   by translating D3D11 bytecode with HLSLcc, which is open source. Player
   builds strip the RDEF reflection chunk HLSLcc needs, so `owport.rdef`
   rebuilds it from Unity's own bind info (stored after each subprogram, format
   in `owport.shaderblob`), `owport.hlslcc` translates to MSL, and
   `owport.shaders` recomputes constant buffer offsets for Metal's layout rules
   (`owport.mslayout`) and writes Metal subprograms in the 2020.3 format.
   Named constant buffers (`UnityPerCamera`, `UnityPerDraw`, ...) keep their
   full D3D11 layout in every stage: Unity's Metal runtime shares a buffer by
   name between the vertex and fragment stages, and HLSLcc's per-stage pruning
   made them read each other's values (the sun's fog drew through the ground).

`tools/validate_blobs.py` round-trips every compiled subprogram of a file
through the blob codec (byte for byte); `build/metalcheck` compiles MSL files
with the system Metal compiler, the same one the player uses.

## Build

Requirements: an Apple silicon Mac, Python 3.11+, CMake, Xcode command line
tools, the .NET 10 SDK (`dotnet` on the PATH). With Homebrew:

```sh
xcode-select --install
brew install python@3.12 cmake dotnet
```

Then, from this folder, point the script at your Windows copy of the game
(the folder that contains `OuterWilds_Data`):

```sh
./setup.sh "/path/to/Outer Wilds"
```

It checks the tools, downloads Unity 2020.3.48f1 (3.4 GB, from Unity's
download archive) and Steamworks.NET 20.1.0 into `deps/` (SHA-256 checked),
builds HLSLcc and the helpers, and writes `Outer Wilds.app` here. Re-running
skips finished steps; `deps/` (about 4.5 GB) can be deleted afterwards.

### Manual build

What `setup.sh` does, step by step. Versions are the ones the build was
tested with.

```sh
python3 -m venv .venv && .venv/bin/pip install UnityPy==1.25.4 lz4==4.4.5
git clone https://github.com/Unity-Technologies/HLSLcc ../work/tools/HLSLcc
git -C ../work/tools/HLSLcc checkout 3ea1fcd6bd0ac445bc078de0bf32f0950188577b
cmake -S ../work/tools/HLSLcc -B ../work/tools/HLSLcc/build -DCMAKE_BUILD_TYPE=Release \
      -DHLSLCC_LIBRARY_SHARED=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build ../work/tools/HLSLcc/build -j
mkdir -p build
clang++ -std=c++14 -O2 -shared -fPIC -I../work/tools/HLSLcc/include \
        -I../work/tools/HLSLcc/src/internal_includes -o build/libhlslcc_bridge.dylib \
        tools/hlslcc_bridge/bridge.cpp ../work/tools/HLSLcc/build/libhlslcc.a
clang -fobjc-arc -O2 tools/metalcheck/metalcheck.m -framework Foundation -framework Metal -o build/metalcheck
dotnet build tools/serialpatch -c Release -o build/serialpatch
```

Unity 2020.3.48f1: the Mac editor package from Unity's download archive
(`https://download.unity3d.com/download_unity/b805b124c6b7/MacEditorInstaller/Unity-2020.3.48f1.pkg`)
can be unpacked without installing (`pkgutil --expand` then `tar -xf` the
payload). Steam: `steam_api.bundle` from the Steamworks.NET 20.1.0 release
(SDK 1.53, universal), which matches the game's Steamworks.NET.

```sh
.venv/bin/python -m owport.build --game "/path/to/Outer Wilds" \
    --unity /path/to/Unity.app --steam-bundle /path/to/steam_api.bundle \
    -o "Outer Wilds.app"
```

Three more incompatibilities between the 2019.4-built game code/data and the
2020.3 runtime are handled by `tools/serialpatch` (Mono.Cecil):

- managed fields 2020.3 serializes but 2019.4 data lacks
  (`PersistentCall.m_TargetAssemblyTypeName`, four uGUI fields) are marked
  `[NonSerialized]`; `tools/serialdiff` lists such changes;
- uGUI is recompiled from the 2020.3 package source (`tools/build_ugui.sh`);
- `fixapi` rewrites the game's `UIVertex.uvN` accesses (Vector2 -> Vector4)
  and retargets types that moved namespace; `serialpatch linkcheck` lists
  unresolved references (multi-dimensional array methods are false positives).

## Port extras

`mod/OWMacExtras` (built by `tools/build_extras.sh`, registered in the two
runtime JSON files) adds an FPS counter (F3 toggles it) and a mouse
multiplier: macOS reports mouse deltas in points, so the mouse is slower than
on Windows. `serialpatch mousemult` makes `BaseInputManager.UpdateMouse`
apply it. They live in the game's own `secretsettings.txt`
(`~/Library/Application Support/unity.Mobius Digital.Outer Wilds/`):

    MacMouseSensitivity=2.0
    MacShowFPS=true
    MacSkipIntro=true

`MacSkipIntro` (patched into `TitleScreenAnimation.Awake` by `serialpatch
skipintro`) starts the title screen as when returning from a game: no gamepad
splash, no camera pan. The build uses Unity's non-development player by
default (`--variant macos_arm64_development_mono` brings back the dev console).

## Performance

`Info.plist` sets two environment variables read by the Unity player
(`LSEnvironment`, so they apply when the app is opened from Finder):

- `UNITY_DISPLAYLINK_BLIT=0` presents the drawable directly instead of blitting
  it from a CVDisplayLink callback. With the blit, CPU and GPU never overlapped
  (one frame in flight, GPU idle ~10 ms per frame): 50-70 FPS in game before,
  over 100 after;
- `UNITY_METAL_SPLITTING_CB_COMMITS=1` commits command buffers during the frame
  so the GPU starts before encoding ends (a further gain on top of the first).

Shadows are the largest GPU cost left (point-light cube shadows, the
directional cascades, the game's proxy shadows). The game's physics runs at
60 Hz; `PhysicsRate=120` in `secretsettings.txt` (a setting of the game itself)
makes movement match a 120 Hz display, at some CPU cost.

The first launch after a build spends a minute or more compiling Metal
shaders (black window); later launches use the system's shader cache.

## Status (2026-10-25)

Verified:
- blob codec round-trips all 10,881 D3D11 and 116 Metal subprograms byte for byte;
- 12,149 subprograms translated (8 fail: a StructuredBuffer in Post FX Eye
  Adaptation); the 10,546 MSL of `globalgamemanagers.assets` all compile with
  the system Metal compiler;
- the app starts natively on arm64, creates the Metal device, loads every
  converted file and runs the game's C# managers.

- with graphics jobs disabled (they deadlock the Metal device), the Mobius
  intro, the title screen with its 3D Timber Hearth scene and the menu render
  natively; a profile copied from the Windows saves is recognised;
- loading a save into the solar system and playing: input, rendering
  (including the sun and planet fog impostors) and saves work, at over 100 FPS
  on an M3 Pro (1512x949 window).

These were checked on a build patched to start without a Steam client. This
repository leaves the game's Steam startup untouched; starting it with the
Steam client running has not been tested yet.

The game pauses when its window is not frontmost (runInBackground is off),
which looks like a black screen. `gfx-disable-mt-rendering` in `boot.config`
is ignored by the Mac player (rendering stays on its own thread).

Known gaps: compute shaders (1, D3D11 only), StructuredBuffer bindings,
geometry/tessellation stages (none in this game), asset bundles in
StreamingAssets are left in 2019.4 format.

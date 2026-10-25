#!/bin/bash
# One-shot setup and build: checks the tools, downloads what the build needs
# (Unity 2020.3.48f1 editor package, Steamworks.NET 20.1.0, HLSLcc), builds the
# helpers, then builds the app from your copy of the game.
#
# usage: ./setup.sh "/path/to/Outer Wilds" ["Outer Wilds.app"]
#
# Re-running is safe: finished steps are skipped. Downloads go to ./deps
# (about 4.5 GB with the Unity package; delete it once the app is built).
# Environment overrides: OWPORT_DEPS (download dir), OWPORT_UNITY_PKG (an
# already downloaded Unity-2020.3.48f1.pkg), OWPORT_JOBS (parallel jobs).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
DEPS="${OWPORT_DEPS:-$REPO/deps}"
GAME="${1:-}"
OUT="${2:-$REPO/Outer Wilds.app}"

UNITY_URL="https://download.unity3d.com/download_unity/b805b124c6b7/MacEditorInstaller/Unity-2020.3.48f1.pkg"
UNITY_SHA256="924f0c281b4a063cef90b68fdf81c379d5412baefa8df62dfd5298a7365a7403"
STEAMWORKS_URL="https://github.com/rlabrecque/Steamworks.NET/releases/download/20.1.0/Steamworks.NET_20.1.0.unitypackage"
STEAMWORKS_SHA256="3be79e6bdf53c86210267bf39f46362bb839130896006aec26364c5efc37e314"
HLSLCC_URL="https://github.com/Unity-Technologies/HLSLcc"
HLSLCC_COMMIT="3ea1fcd6bd0ac445bc078de0bf32f0950188577b"

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found. $2"; }
check_sha() {
    local got
    got=$(shasum -a 256 "$1" | cut -d' ' -f1)
    [ "$got" = "$2" ] || die "$1: SHA-256 $got, expected $2 (delete it and re-run)"
}

# --- Checks ---------------------------------------------------------------
[ -n "$GAME" ] || die "usage: $0 \"/path/to/Outer Wilds\" [\"Outer Wilds.app\"]"
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || die "this builds an Apple silicon (arm64) macOS app; run it on such a Mac"
[ -f "$GAME/OuterWilds_Data/globalgamemanagers" ] \
    || die "$GAME does not look like the Windows Outer Wilds folder (no OuterWilds_Data/globalgamemanagers)"
grep -aq "2019.4.39f1" "$GAME/OuterWilds_Data/globalgamemanagers" \
    || die "this game build is not Unity 2019.4.39f1 (Outer Wilds 1.1.13 for Windows); other versions are untested"

step "Checking tools"
xcode-select -p >/dev/null 2>&1 || die "Xcode command line tools missing: xcode-select --install"
need git "Install the Xcode command line tools: xcode-select --install"
need curl ""
need cmake "Install it, e.g. brew install cmake"
need dotnet "Install the .NET 10 SDK, e.g. brew install dotnet"
dotnet --list-sdks | grep -q '^10\.' || die "the .NET 10 SDK is required (dotnet --list-sdks shows none)"
echo "ok: cmake $(cmake --version | head -1 | awk '{print $3}'), .NET $(dotnet --version)"
mkdir -p "$DEPS" "$REPO/build"

# --- Python environment ---------------------------------------------------
# The first Python >= 3.11 that can create a working venv (some managed
# interpreters cannot).
if [ ! -x "$REPO/.venv/bin/python" ]; then
    step "Creating the Python environment"
    for c in python3 python3.13 python3.12 python3.11; do
        command -v "$c" >/dev/null 2>&1 || continue
        "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || continue
        if "$c" -m venv "$REPO/.venv" >/dev/null 2>&1 && "$REPO/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
            echo "ok: $("$REPO/.venv/bin/python" --version) ($(command -v "$c"))"
            break
        fi
        rm -rf "$REPO/.venv"
    done
    [ -x "$REPO/.venv/bin/python" ] || die "no Python 3.11+ able to create a venv; install one, e.g. brew install python@3.12"
fi
"$REPO/.venv/bin/pip" install -q UnityPy==1.25.4 lz4==4.4.5

# --- HLSLcc and the bridge ------------------------------------------------
if [ ! -f "$REPO/build/libhlslcc_bridge.dylib" ]; then
    step "Building HLSLcc"
    H="$DEPS/HLSLcc"
    [ -d "$H/.git" ] || git clone -q "$HLSLCC_URL" "$H"
    git -C "$H" checkout -q "$HLSLCC_COMMIT"
    cmake -S "$H" -B "$H/build" -DCMAKE_BUILD_TYPE=Release -DHLSLCC_LIBRARY_SHARED=OFF \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null
    cmake --build "$H/build" -j >/dev/null
    clang++ -std=c++14 -O2 -shared -fPIC -I"$H/include" -I"$H/src/internal_includes" \
            -o "$REPO/build/libhlslcc_bridge.dylib" "$REPO/tools/hlslcc_bridge/bridge.cpp" "$H/build/libhlslcc.a"
fi

# --- serialpatch (Mono.Cecil) ---------------------------------------------
if [ ! -f "$REPO/build/serialpatch/serialpatch.dll" ]; then
    step "Building serialpatch"
    dotnet build "$REPO/tools/serialpatch" -c Release -o "$REPO/build/serialpatch" -v quiet -nologo
fi

# --- Unity 2020.3.48f1 (only the parts the build reads) -------------------
UNITY_APP="$DEPS/unity/Unity/Unity.app"
if [ ! -d "$UNITY_APP/Contents/PlaybackEngines/MacStandaloneSupport/Variations/macos_arm64_nondevelopment_mono" ]; then
    PKG="${OWPORT_UNITY_PKG:-$DEPS/Unity-2020.3.48f1.pkg}"
    if [ ! -f "$PKG" ]; then
        step "Downloading Unity 2020.3.48f1 (3.4 GB, from Unity's download archive; resumable)"
        curl -fL -C - --retry 3 -o "$PKG.part" "$UNITY_URL"
        mv "$PKG.part" "$PKG"
    fi
    step "Checking and unpacking Unity (players, Mono, uGUI source)"
    check_sha "$PKG" "$UNITY_SHA256"
    rm -rf "$DEPS/unity"
    mkdir -p "$DEPS/unity"
    C="./Unity/Unity.app/Contents"
    tar -xOf "$PKG" Unity.pkg.tmp/Payload | tar -xf - -C "$DEPS/unity" \
        --exclude "$C/PlaybackEngines/MacStandaloneSupport/Variations/macos_x64*" \
        "$C/PlaybackEngines/MacStandaloneSupport" "$C/MonoBleedingEdge" \
        "$C/Resources/PackageManager/BuiltInPackages/com.unity.ugui"
fi

# --- Steamworks.NET 20.1.0: steam_api.bundle ------------------------------
STEAM_BUNDLE="$DEPS/steam_api.bundle"
if [ ! -f "$STEAM_BUNDLE/Contents/MacOS/libsteam_api.dylib" ]; then
    step "Downloading Steamworks.NET 20.1.0 (steam_api.bundle)"
    SW="$DEPS/Steamworks.NET_20.1.0.unitypackage"
    [ -f "$SW" ] || curl -fsSL --retry 3 -o "$SW" "$STEAMWORKS_URL"
    check_sha "$SW" "$STEAMWORKS_SHA256"
    T=$(mktemp -d)
    tar -xzf "$SW" -C "$T"
    rm -rf "$STEAM_BUNDLE"
    for d in "$T"/*; do
        [ -f "$d/asset" ] && [ -f "$d/pathname" ] || continue
        p=$(head -1 "$d/pathname")
        case "$p" in
            */Plugins/steam_api.bundle/*)
                rel="${p#*/Plugins/steam_api.bundle/}"
                mkdir -p "$STEAM_BUNDLE/$(dirname "$rel")"
                cp "$d/asset" "$STEAM_BUNDLE/$rel" ;;
        esac
    done
    rm -rf "$T"
    [ -f "$STEAM_BUNDLE/Contents/MacOS/libsteam_api.dylib" ] || die "steam_api.bundle not found in the Steamworks.NET package"
fi

# --- Build ----------------------------------------------------------------
JOBS="${OWPORT_JOBS:-$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || echo 4)}"
step "Building $OUT (5-10 minutes)"
cd "$REPO"
"$REPO/.venv/bin/python" -m owport.build --game "$GAME" --unity "$UNITY_APP" \
    --steam-bundle "$STEAM_BUNDLE" -o "$OUT" -j "$JOBS"

cat <<EOF

Done: $OUT
- Start Steam and sign in with the account that owns Outer Wilds, then open the app.
- The first launch compiles the Metal shaders: a black window for a minute or more.
- Keep the app to yourself: it contains the game's data and Unity's runtime.
- $DEPS can be deleted now (about 4.5 GB); re-running this script downloads it again.
EOF

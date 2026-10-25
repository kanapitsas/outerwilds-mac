#!/bin/bash
# Compile Unity UI (com.unity.ugui) from the 2020.3 editor's built-in package
# source against the 2020.3 engine assemblies. The game's own UnityEngine.UI.dll
# was compiled against 2019.4 (UIVertex.uv0..3 were Vector2 then) and fails at
# JIT time on 2020.3.
# usage: tools/build_ugui.sh <Unity.app> <out dir>
set -euo pipefail
E="$1/Contents"; O="$2"
# Roslyn from the newest installed .NET SDK ("10.0.402 [/path/to/sdk]").
SDK=$(dotnet --list-sdks | tail -1 | sed -E 's/^([^ ]+) \[(.*)\]$/\2\/\1/')
CSC="$SDK/Roslyn/bincore/csc.dll"
SRC="$E/Resources/PackageManager/BuiltInPackages/com.unity.ugui/Runtime"
MAN="$E/PlaybackEngines/MacStandaloneSupport/Variations/mono/Managed"
JIT="$E/MonoBleedingEdge/lib/mono/unityjit"
mkdir -p "$O"
RSP="$O/csc.rsp"
{
  echo "-nologo -nostdlib -target:library -optimize+ -langversion:8 -deterministic"
  echo "-nowarn:0618,0649,0414,0169,1701,0672"
  echo "-define:PACKAGE_PHYSICS;PACKAGE_PHYSICS2D;PACKAGE_TILEMAP;PACKAGE_ANIMATION;UNITY_STANDALONE_OSX;UNITY_STANDALONE"
  echo "\"-out:$O/UnityEngine.UI.dll\""
  for f in "$MAN"/UnityEngine*.dll "$JIT/mscorlib.dll" "$JIT/System.dll" "$JIT/System.Core.dll" "$JIT/Facades/netstandard.dll"; do
    echo "\"-r:$f\""
  done
  find "$SRC" -name "*.cs" | sed 's/.*/"&"/'
} > "$RSP"
dotnet "$CSC" "@$RSP"

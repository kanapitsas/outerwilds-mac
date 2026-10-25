#!/bin/bash
# Compile mod/OWMacExtras against the app's managed assemblies.
# usage: tools/build_extras.sh <app Managed dir> <out dir>
set -euo pipefail
MAN="$1"; O="$2"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Roslyn from the newest installed .NET SDK ("10.0.402 [/path/to/sdk]").
SDK=$(dotnet --list-sdks | tail -1 | sed -E 's/^([^ ]+) \[(.*)\]$/\2\/\1/')
CSC="$SDK/Roslyn/bincore/csc.dll"
mkdir -p "$O"
RSP="$O/extras.rsp"
{
  echo "-nologo -nostdlib -target:library -optimize+ -langversion:8 -deterministic"
  echo "\"-out:$O/OWMacExtras.dll\""
  for f in mscorlib System System.Core netstandard UnityEngine.CoreModule UnityEngine.IMGUIModule UnityEngine.TextRenderingModule Unity.InputSystem; do
    echo "\"-r:$MAN/$f.dll\""
  done
  find "$REPO/mod/OWMacExtras" -name "*.cs" | sed 's/.*/"&"/'
} > "$RSP"
dotnet "$CSC" "@$RSP"

"""Assemble a native arm64 macOS Outer Wilds.app from the Windows game files.

    python -m owport.build --game "<dir with OuterWilds_Data>" --unity <Unity 2020.3.48f1 Unity.app> -o OuterWilds.app
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import plistlib
import shutil
import subprocess
import sys
import time

from . import convert_file, runtime_json, shaders

SERIALIZED_SUFFIXES = (".assets",)
SERIALIZED_NAMES = {"globalgamemanagers", "unity_builtin_extra"}
# Windows-only native plugins the Mac player cannot load.
DROP_PLUGINS = True


def is_serialized(name: str) -> bool:
    return name.endswith(SERIALIZED_SUFFIXES) or name in SERIALIZED_NAMES or (
        name.startswith("level") and name[5:].isdigit())


def _parent(path: str) -> str:
    return os.path.dirname(os.path.abspath(path))


def clone(src: str, dst: str) -> None:
    """APFS clone when possible (free), plain copy otherwise."""
    os.makedirs(_parent(dst), exist_ok=True)
    if subprocess.run(["cp", "-c", src, dst], capture_output=True).returncode != 0:
        shutil.copy2(src, dst)


def clone_tree(src: str, dst: str) -> None:
    os.makedirs(_parent(dst), exist_ok=True)
    if subprocess.run(["cp", "-c", "-R", src, dst], capture_output=True).returncode != 0:
        shutil.copytree(src, dst, dirs_exist_ok=True)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Managed fields 2020.3 serializes that 2019.4 data does not contain.
NONSERIALIZED_FIELDS = {
    "UnityEngine.CoreModule.dll": ["UnityEngine.Events.PersistentCall::m_TargetAssemblyTypeName"],
    "UnityEngine.UI.dll": [
        "UnityEngine.UI.Graphic::m_RaycastPadding",
        "UnityEngine.UI.Navigation::m_WrapAround",
        "UnityEngine.UI.HorizontalOrVerticalLayoutGroup::m_ReverseArrangement",
        "UnityEngine.UI.CanvasScaler::m_PresetInfoIsWorld",
    ],
}
# Game assemblies compiled against 2019.4 APIs that changed in 2020.3.
FIXAPI_ASSEMBLIES = ["Assembly-CSharp.dll", "Unity.TextMeshPro.dll", "Unity.Timeline.dll"]


def write_boot_config(path: str) -> None:
    """Graphics jobs (on in the Windows build) deadlock the 2020.3 Metal device:
    the main thread waits on a CPU fence the gfx worker never signals.
    gfx-disable-mt-rendering is ignored by the Mac player (every log still says
    "threaded=1"; the threading choice only reads force-gfx-*), so the startup race
    once seen with the release player (2 hangs in 5 launches) was not cured by it.
    Kept as harmless; force-gfx-direct=1 would be the real switch, at a CPU cost."""
    with open(path) as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    keep = [l for l in lines if not l.startswith(("gfx-enable-gfx-jobs=", "gfx-enable-native-gfx-jobs=",
                                                   "gfx-disable-mt-rendering="))]
    keep += ["gfx-enable-gfx-jobs=0", "gfx-enable-native-gfx-jobs=0", "gfx-disable-mt-rendering=1"]
    os.remove(path)  # may be an APFS clone of the game's file
    with open(path, "w") as f:
        f.write("\n".join(keep) + "\n")


def _dotnet(*args: str) -> None:
    subprocess.run(["dotnet", os.path.join(REPO, "build", "serialpatch", "serialpatch.dll"), *args], check=True)


def patch_assemblies(managed: str, unity_app: str) -> None:
    ugui = os.path.join(REPO, "build", "ugui", "UnityEngine.UI.dll")
    if not os.path.exists(ugui):
        subprocess.run([os.path.join(REPO, "tools", "build_ugui.sh"), unity_app, os.path.dirname(ugui)], check=True)
    clone(ugui, os.path.join(managed, "UnityEngine.UI.dll"))
    for dll, fields in NONSERIALIZED_FIELDS.items():
        path = os.path.join(managed, dll)
        _dotnet("mark", path, path + ".patched", *fields)
        os.replace(path + ".patched", path)
    for dll in FIXAPI_ASSEMBLIES:
        path = os.path.join(managed, dll)
        _dotnet("fixapi", path, path + ".patched", managed)
        os.replace(path + ".patched", path)
    # Port extras (FPS counter, mouse multiplier): mod/OWMacExtras.
    extras = os.path.join(REPO, "build", "extras")
    subprocess.run([os.path.join(REPO, "tools", "build_extras.sh"), managed, extras], check=True)
    shutil.copy2(os.path.join(extras, "OWMacExtras.dll"), os.path.join(managed, "OWMacExtras.dll"))
    path = os.path.join(managed, "Assembly-CSharp.dll")
    _dotnet("mousemult", path, path + ".patched", managed)
    os.replace(path + ".patched", path)
    _dotnet("skipintro", path, path + ".patched", managed)
    os.replace(path + ".patched", path)


def _convert_job(args: tuple[str, str]) -> tuple[str, dict, int, int, list]:
    src, dst = args
    st = shaders.Stats()
    stats = convert_file.convert(src, dst, st, verbose=False)
    return os.path.basename(src), dict(stats), st.translated, st.failed, st.errors.most_common(5)


def build(game_dir: str, unity_app: str, out_app: str, jobs: int, variant: str, steam_bundle: str | None) -> None:
    t0 = time.time()
    data_src = os.path.join(game_dir, "OuterWilds_Data")
    support = os.path.join(unity_app, "Contents/PlaybackEngines/MacStandaloneSupport")
    player = os.path.join(support, "Variations", variant, "UnityPlayer.app")
    entry = os.path.join(support, "Source/Player/MacPlayer/MacPlayerEntryPoint")

    if os.path.exists(out_app):
        shutil.rmtree(out_app)
    clone_tree(player, out_app)
    contents = os.path.join(out_app, "Contents")
    res = os.path.join(contents, "Resources")
    data = os.path.join(res, "Data")

    os.makedirs(os.path.join(res, "MainMenu.nib"), exist_ok=True)
    clone(os.path.join(entry, "Resources/MainMenu.nib/keyedobjects.nib"), os.path.join(res, "MainMenu.nib/keyedobjects.nib"))
    clone(os.path.join(entry, "Resources/PlayerIcon.icns"), os.path.join(res, "PlayerIcon.icns"))
    with open(os.path.join(entry, "Info.plist"), "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleExecutable": "UnityPlayer",
        "CFBundleIdentifier": "unity.Mobius Digital.Outer Wilds",
        "CFBundleName": "Outer Wilds",
        "CFBundleGetInfoString": "Outer Wilds (native macOS build made from the Windows release)",
        "CFBundleShortVersionString": "1.1.13",
        "CFBundleVersion": "1",
        "LSApplicationCategoryType": "public.app-category.adventure-games",
        "UnityBuildNumber": "b805b124c6b7",
        "NSHighResolutionCapable": False,
        # SteamAppId/SteamGameId let SteamAPI_Init find the app when started from Finder; Steam
        # still checks the license. UNITY_DISPLAYLINK_BLIT=0: present the drawable directly instead
        # of blitting from a CVDisplayLink callback, which kept CPU and GPU from overlapping
        # (in game: 50-70 FPS before, over 100 after). UNITY_METAL_SPLITTING_CB_COMMITS=1: commit
        # command buffers during the frame so the GPU starts before encoding ends (better again).
        "LSEnvironment": {"SteamAppId": "753640", "SteamGameId": "753640", "UNITY_DISPLAYLINK_BLIT": "0",
                          "UNITY_METAL_SPLITTING_CB_COMMITS": "1"},
    })
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(info, f)

    clone_tree(os.path.join(support, "MonoBleedingEdge"), os.path.join(contents, "MonoBleedingEdge"))

    jobs_list = []
    for root, dirs, files in os.walk(data_src):
        rel = os.path.relpath(root, data_src)
        if rel.split(os.sep)[0] == "Plugins" and DROP_PLUGINS:
            continue
        for fn in files:
            src = os.path.join(root, fn)
            dst = os.path.join(data, rel, fn) if rel != "." else os.path.join(data, fn)
            if rel == "Resources" and fn == "unity default resources":
                continue  # the player ships its own, with Metal shaders
            if rel == "." and is_serialized(fn) or rel == "Resources" and fn == "unity_builtin_extra":
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                jobs_list.append((src, dst))
            else:
                clone(src, dst)

    # 2020.3 engine assemblies replace the 2019.4 ones the game shipped with.
    managed_src = os.path.join(support, "Variations", "mono", "Managed")
    for fn in os.listdir(managed_src):
        if fn.endswith(".dll"):
            clone(os.path.join(managed_src, fn), os.path.join(data, "Managed", fn))

    write_boot_config(os.path.join(data, "boot.config"))
    patch_assemblies(os.path.join(data, "Managed"), unity_app)
    runtime_json.write(os.path.join(data_src, "globalgamemanagers"), data)

    if steam_bundle:
        # Steamworks.NET in the game P/Invokes "steam_api64" (its Windows name).
        clone_tree(steam_bundle, os.path.join(contents, "PlugIns", "steam_api64.bundle"))

    # Largest files first so the pool stays busy.
    jobs_list.sort(key=lambda j: -os.path.getsize(j[0]))
    total_t = total_f = 0
    with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
        for name, stats, tr, fl, errs in pool.map(_convert_job, jobs_list):
            total_t += tr
            total_f += fl
            print(f"  {name}: {stats} shaders ok={tr} failed={fl}", flush=True)
            for e, n in errs:
                print(f"     {n}x {e}", flush=True)
    print(f"shader subprograms translated {total_t}, failed {total_f}")

    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", out_app], check=True)
    print(f"built {out_app} in {time.time() - t0:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, help="folder containing OuterWilds_Data")
    ap.add_argument("--unity", required=True, help="Unity 2020.3.48f1 Unity.app (editor install or extracted pkg)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--variant", default="macos_arm64_nondevelopment_mono",
                    help="Unity player variant; macos_arm64_development_mono adds the dev console and profiler hooks")
    ap.add_argument("--steam-bundle", help="steam_api.bundle from Steamworks.NET 20.1.0 (SDK 1.53)")
    a = ap.parse_args()
    build(a.game, a.unity, a.output, a.jobs, a.variant, a.steam_bundle)


if __name__ == "__main__":
    sys.exit(main())

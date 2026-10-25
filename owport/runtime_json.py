"""Write the two JSON files 2020.3 players read instead of globalgamemanagers data.

2019.4 kept the assembly list in MonoManager and the [RuntimeInitializeOnLoadMethod]
table in RuntimeInitializeOnLoadManager; 2020.3 dropped both and reads
Data/ScriptingAssemblies.json and Data/RuntimeInitializeOnLoads.json.
"""

from __future__ import annotations

import json
import os

import UnityPy

from . import upgrade

# RuntimeInitializeLoadType values
AFTER_SCENE_LOAD, BEFORE_SCENE_LOAD, AFTER_ASSEMBLIES_LOADED, BEFORE_SPLASH_SCREEN, SUBSYSTEM_REGISTRATION = range(5)

_ORDER_LISTS = {
    "m_BeforeUnityMethodExecutionOrders": BEFORE_SCENE_LOAD,
    "m_BeforeMethodExecutionOrders": BEFORE_SCENE_LOAD,
    "m_AfterUnityMethodExecutionOrders": AFTER_SCENE_LOAD,
    "m_AfterMethodExecutionOrders": AFTER_SCENE_LOAD,
    "m_AfterAssembliesLoadedUnityMethodExecutionOrders": AFTER_ASSEMBLIES_LOADED,
    "m_AfterAssembliesLoadedMethodExecutionOrders": AFTER_ASSEMBLIES_LOADED,
    "m_BeforeSplashScreenUnityMethodExecutionOrders": BEFORE_SPLASH_SCREEN,
    "m_BeforeSplashScreenMethodExecutionOrders": BEFORE_SPLASH_SCREEN,
    "m_SubsystemRegistrationUnityMethodExecutionOrders": SUBSYSTEM_REGISTRATION,
    "m_SubsystemRegistrationMethodExecutionOrders": SUBSYSTEM_REGISTRATION,
}


# Assemblies added by this port (mod/), with their [RuntimeInitializeOnLoadMethod]s.
EXTRA_ASSEMBLIES = ["OWMacExtras.dll"]
EXTRA_INIT_METHODS = [("OWMacExtras", "OWMacExtras", "Extras", "Init", BEFORE_SCENE_LOAD)]


def write(globalgamemanagers_2019: str, data_dir: str) -> None:
    env = UnityPy.load(globalgamemanagers_2019)
    objs = {o.type.name: o for o in env.objects}
    mono = objs["MonoManager"].read_typetree(nodes=upgrade.tree(116, upgrade.OLD_VERSION))
    with open(os.path.join(data_dir, "ScriptingAssemblies.json"), "w") as f:
        names = list(mono["m_AssemblyNames"]) + EXTRA_ASSEMBLIES
        types = list(mono["m_AssemblyTypes"]) + [16] * len(EXTRA_ASSEMBLIES)  # 16: user assembly
        json.dump({"names": names, "types": types}, f)

    rio = objs["RuntimeInitializeOnLoadManager"].read_typetree(nodes=upgrade.tree(300, upgrade.OLD_VERSION))
    load_type = {}
    for key, lt in _ORDER_LISTS.items():
        for idx in rio.get(key, []):
            load_type[idx] = lt
    root = []
    for idx, method in enumerate(rio["m_ClassMethodInfos"]):
        cls = rio["m_ClassInfos"][method["m_ClassIndex"]]
        root.append({
            "assemblyName": rio["m_AssemblyNames"][cls["m_AssemblyNameIndex"]],
            "nameSpace": rio["m_NamespaceNames"][cls["m_NamespaceIndex"]],
            "className": cls["m_ClassName"],
            "methodName": method["m_MethodName"],
            "loadTypes": load_type.get(idx, AFTER_SCENE_LOAD),
            "isUnityClass": cls["m_IsUnityClass"],
        })
    for asm, ns, cls, method, lt in EXTRA_INIT_METHODS:
        root.append({"assemblyName": asm, "nameSpace": ns, "className": cls, "methodName": method,
                     "loadTypes": lt, "isUnityClass": False})
    with open(os.path.join(data_dir, "RuntimeInitializeOnLoads.json"), "w") as f:
        json.dump({"root": root}, f)

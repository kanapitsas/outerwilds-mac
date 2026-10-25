// Small additions for the macOS build of Outer Wilds, loaded next to the game's
// own assemblies (listed in ScriptingAssemblies.json, started from
// RuntimeInitializeOnLoads.json):
//  - an FPS counter (F3 toggles it);
//  - a mouse sensitivity multiplier. macOS reports mouse deltas in points, not
//    pixels, so the mouse feels slower than on Windows at the same in-game
//    setting. BaseInputManager.UpdateMouse is patched to multiply by it.
//  - skipping the intro (gamepad splash and title camera pan) at launch;
//    TitleScreenAnimation.Awake is patched to read SkipIntro.
// All are set in secretsettings.txt, the game's own advanced settings file:
//    MacShowFPS=true
//    MacMouseSensitivity=2.0
//    MacSkipIntro=true

using System;
using System.Globalization;
using System.IO;
using UnityEngine;
using UnityEngine.InputSystem;

namespace OWMacExtras
{
    public static class Extras
    {
        public static float MouseMultiplier = 2f;
        public static bool ShowFps = true;
        public static bool SkipIntro = true;

        const string KeyFps = "MacShowFPS";
        const string KeyMouse = "MacMouseSensitivity";
        const string KeySkipIntro = "MacSkipIntro";

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        static void Init()
        {
            try
            {
                LoadSettings(Path.Combine(Application.persistentDataPath, "secretsettings.txt"));
            }
            catch (Exception e)
            {
                Debug.LogWarning("[OWMacExtras] settings not read: " + e.Message);
            }
            var go = new GameObject("OWMacExtras");
            UnityEngine.Object.DontDestroyOnLoad(go);
            go.hideFlags = HideFlags.HideInHierarchy;
            go.AddComponent<FpsOverlay>();
            Debug.Log($"[OWMacExtras] mouse x{MouseMultiplier}, fps counter {(ShowFps ? "on" : "off")} (F3), skip intro {SkipIntro}");
        }

        static void LoadSettings(string path)
        {
            bool hasFps = false, hasMouse = false, hasSkip = false;
            if (File.Exists(path))
            {
                foreach (var raw in File.ReadAllLines(path))
                {
                    string line = raw.Trim();
                    if (line.StartsWith("//")) continue;
                    int c = line.IndexOf("//", StringComparison.Ordinal);
                    if (c >= 0) line = line.Substring(0, c);
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim();
                    string val = line.Substring(eq + 1).Trim();
                    if (key.Equals(KeyFps, StringComparison.OrdinalIgnoreCase))
                    {
                        hasFps = true;
                        if (bool.TryParse(val, out bool b)) ShowFps = b;
                    }
                    else if (key.Equals(KeySkipIntro, StringComparison.OrdinalIgnoreCase))
                    {
                        hasSkip = true;
                        if (bool.TryParse(val, out bool b)) SkipIntro = b;
                    }
                    else if (key.Equals(KeyMouse, StringComparison.OrdinalIgnoreCase))
                    {
                        hasMouse = true;
                        if (float.TryParse(val, NumberStyles.Float, CultureInfo.InvariantCulture, out float f) && f > 0f && f <= 20f)
                            MouseMultiplier = f;
                    }
                }
            }
            // Document the options in the file the first time, as the game does for its own.
            if (!hasFps || !hasMouse || !hasSkip)
            {
                string add = "\n";
                if (!hasMouse)
                    add += "// macOS build: mouse sensitivity multiplier, on top of Look Sensitivity in Options.\n" +
                           "// macOS reports mouse movement in points (half the pixels on Retina screens).\n" +
                           "// Default value: 2.0   Valid values: 0.1 to 20\n" +
                           KeyMouse + "=2.0\n";
                if (!hasFps)
                    add += "// macOS build: show the frame rate in the top-left corner (F3 toggles it in game).\n" +
                           "// Default value: true   Valid values: true, false\n" +
                           KeyFps + "=true\n";
                if (!hasSkip)
                    add += "// macOS build: skip the gamepad splash and the title camera pan at launch.\n" +
                           "// Default value: true   Valid values: true, false\n" +
                           KeySkipIntro + "=true\n";
                File.AppendAllText(path, add);
            }
        }
    }

    public class FpsOverlay : MonoBehaviour
    {
        float _accum;
        int _frames;
        float _fps;
        float _ms;
        GUIStyle _style;
        Texture2D _bg;

        void Update()
        {
            var kb = Keyboard.current;
            if (kb != null && kb.f3Key.wasPressedThisFrame)
                Extras.ShowFps = !Extras.ShowFps;

            _accum += Time.unscaledDeltaTime;
            _frames++;
            if (_accum >= 0.5f)
            {
                _fps = _frames / _accum;
                _ms = 1000f * _accum / _frames;
                _accum = 0f;
                _frames = 0;
            }
        }

        void OnGUI()
        {
            if (!Extras.ShowFps) return;
            if (_style == null)
            {
                _style = new GUIStyle(GUI.skin.label) { fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleLeft };
                _bg = new Texture2D(1, 1);
                _bg.SetPixel(0, 0, new Color(0f, 0f, 0f, 0.55f));
                _bg.Apply();
            }
            _style.fontSize = Mathf.Max(12, Screen.height / 50);
            string text = $"{Mathf.RoundToInt(_fps)} FPS  {_ms:0.0} ms";
            Vector2 size = _style.CalcSize(new GUIContent(text));
            var rect = new Rect(12f, 12f, size.x + 16f, size.y + 6f);
            GUI.DrawTexture(rect, _bg);
            _style.normal.textColor = _fps >= 55f ? new Color(0.6f, 1f, 0.6f) : _fps >= 30f ? new Color(1f, 0.85f, 0.4f) : new Color(1f, 0.45f, 0.4f);
            GUI.Label(new Rect(rect.x + 8f, rect.y + 3f, size.x, size.y), text, _style);
        }
    }
}

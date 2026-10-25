// The harness's own KSP plugin (GameData/harness-grader). Read-only toward the game except for
// two harness chores: it opens this run's sandbox save at the main menu (creating it the first
// time), and it saves the game when hostctl asks (before a stop). It never acts for the player.
//
// Files, in <KSP>/harness/ (not visible to the agent):
//   state.json     rewritten every second: game time, scene, cheats, every vessel's situation,
//                  orbit, energy and crew, the crew roster, and counts of the player's actions
//   request-save   hostctl creates it; the plugin saves the game, deletes it, writes saved.txt
//   saved.txt      the boot id, real time and outcome of the last save made on request
//
// Actions are counted from the game's own events for things a player does (whatever drove them:
// a person, or a mod acting through the player's controls): staging, picking, attaching and
// deleting parts in the editor, launching, EVA, boarding, switching vessels, adding maneuver
// nodes, changing time warp, deploying experiments, planting flags, recovering vessels. Not
// counted, since they are continuous: throttle and attitude inputs. Those show as activity
// ("controls": seconds with a non-zero throttle or attitude input on the active vessel).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

namespace HarnessGrader
{
    [KSPAddon(KSPAddon.Startup.Instantly, true)]
    public class Grader : MonoBehaviour
    {
        const string SaveFolder = "harness";
        string dir, boot;
        float nextSample, nextRequest;
        long seq;
        bool opened;
        int warpIndex;
        readonly Dictionary<string, long> actions = new Dictionary<string, long>();
        readonly Dictionary<string, long> activity = new Dictionary<string, long>();
        string lastError = "";

        void Awake()
        {
            DontDestroyOnLoad(this);
            dir = Path.Combine(KSPUtil.ApplicationRootPath, "harness");
            Directory.CreateDirectory(dir);
            boot = Guid.NewGuid().ToString("N").Substring(0, 12);
            GameEvents.onStageActivate.Add(s => Count("stage"));
            GameEvents.onEditorPartEvent.Add((t, p) =>
            {
                if (t == ConstructionEventType.PartAttached) Count("part_attach");
                else if (t == ConstructionEventType.PartDeleted) Count("part_delete");
                else if (t == ConstructionEventType.PartCreated) Count("part_pick");
            });
            GameEvents.onLaunch.Add(r => Count("launch"));
            GameEvents.onCrewOnEva.Add(a => Count("eva"));
            GameEvents.onCrewBoardVessel.Add(a => Count("board"));
            GameEvents.onVesselSwitching.Add((a, b) => Count("switch_vessel"));
            GameEvents.onManeuverAdded.Add((v, s) => Count("maneuver_node"));
            GameEvents.onTimeWarpRateChanged.Add(() =>
            {
                // Raised on scene loads too; only a change of rate is a player's action.
                if (TimeWarp.CurrentRateIndex != warpIndex && HighLogic.LoadedScene != GameScenes.LOADING) Count("time_warp");
                warpIndex = TimeWarp.CurrentRateIndex;
            });
            GameEvents.OnExperimentDeployed.Add(d => Count("experiment"));
            GameEvents.onFlagPlant.Add(v => Count("plant_flag"));
            GameEvents.onVesselRecovered.Add((v, b) => Count("recover"));
            GameEvents.onLevelWasLoadedGUIReady.Add(OnScene);
        }

        void Count(string kind)
        {
            long n;
            actions.TryGetValue(kind, out n);
            actions[kind] = n + 1;
        }

        void OnScene(GameScenes scene)
        {
            if (scene != GameScenes.MAINMENU || opened) return;
            opened = true;
            try
            {
                Game game;
                bool exists = File.Exists(Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + SaveFolder + "/persistent.sfs"));
                if (!exists)
                {
                    // A new sandbox save, with the first-visit tips of the space center, editor and
                    // tracking station marked as seen (as a start save would have them).
                    var created = GamePersistence.CreateNewGame(SaveFolder, Game.Modes.SANDBOX, new GameParameters(),
                                                                "Squad/Flags/default", GameScenes.SPACECENTER, EditorFacility.None);
                    GamePersistence.SaveGame(created, "persistent", SaveFolder, SaveMode.OVERWRITE);
                    string sfs = Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + SaveFolder + "/persistent.sfs");
                    // The intro's scenario module is added only when the game starts, so add its
                    // node to the save now, already complete.
                    var root = ConfigNode.Load(sfs);
                    var gameNode = root.GetNode("GAME");
                    var intro = gameNode.AddNode("SCENARIO");
                    intro.AddValue("name", "ScenarioNewGameIntro");
                    intro.AddValue("scene", "5, 6, 8");
                    foreach (var k in new[] { "kscComplete", "editorComplete", "tsComplete" }) intro.AddValue(k, "True");
                    root.Save(sfs);
                }
                game = GamePersistence.LoadGame("persistent", SaveFolder, true, false);
                if (game == null) { lastError = "could not open the save " + SaveFolder; return; }
                HighLogic.CurrentGame = game;
                HighLogic.SaveFolder = SaveFolder;
                game.Start();
            }
            catch (Exception e) { lastError = "opening the save: " + e; Debug.LogError("[harness-grader] " + e); }
        }

        void Update()
        {
            float now = Time.realtimeSinceStartup;
            if (now >= nextRequest)
            {
                nextRequest = now + 0.5f;
                try { Requests(); }
                catch (Exception e) { lastError = "request: " + e.Message; }
            }
            if (now >= nextSample)
            {
                nextSample = now + 1f;
                try { Sample(); if (lastError.StartsWith("sample: ")) lastError = ""; }   // a later good sample clears it
                catch (Exception e) { lastError = "sample: " + e.Message; }
            }
        }

        void Requests()
        {
            string req = Path.Combine(dir, "request-save");
            if (!File.Exists(req)) return;
            File.Delete(req);
            string result;
            try
            {
                var s = HighLogic.LoadedScene;
                if (HighLogic.CurrentGame == null || !(s == GameScenes.FLIGHT || s == GameScenes.SPACECENTER
                    || s == GameScenes.EDITOR || s == GameScenes.TRACKSTATION))
                    result = "not saved: no game open (scene " + s + ")";
                else
                    result = "saved: " + GamePersistence.SaveGame("persistent", HighLogic.SaveFolder, SaveMode.OVERWRITE);
            }
            catch (Exception e) { result = "save failed: " + e.Message; }
            File.WriteAllText(Path.Combine(dir, "saved.txt"), boot + " " + DateTime.UtcNow.ToString("o") + " " + result);
        }

        void Sample()
        {
            var j = new Json();
            j.Begin();
            j.Str("boot", boot); j.Num("seq", ++seq);
            j.Num("real_time", (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds);
            j.Str("scene", HighLogic.LoadedScene.ToString());
            j.Str("save_folder", HighLogic.SaveFolder ?? "");
            j.Str("mode", HighLogic.CurrentGame != null ? HighLogic.CurrentGame.Mode.ToString() : "");
            j.Str("error", lastError);
            bool inGame = HighLogic.CurrentGame != null && HighLogic.LoadedScene != GameScenes.MAINMENU
                          && HighLogic.LoadedScene != GameScenes.LOADING;
            if (inGame)
            {
                j.Num("ut", Planetarium.GetUniversalTime());
                j.Num("warp", TimeWarp.CurrentRate);
            }
            j.Key("cheats"); j.Begin();
            j.Bool("InfinitePropellant", CheatOptions.InfinitePropellant);
            j.Bool("InfiniteElectricity", CheatOptions.InfiniteElectricity);
            j.Bool("NoCrashDamage", CheatOptions.NoCrashDamage);
            j.Bool("IgnoreMaxTemperature", CheatOptions.IgnoreMaxTemperature);
            j.Bool("UnbreakableJoints", CheatOptions.UnbreakableJoints);
            j.Bool("MiddleMouseClickSetPosition", CheatOptions.MiddleMouseClickSetPosition);
            j.Bool("IgnoreEVAConstructionMassLimit", CheatOptions.IgnoreEVAConstructionMassLimit);
            j.Bool("IgnoreKerbalInventoryLimits", CheatOptions.IgnoreKerbalInventoryLimits);
            j.End();
            var active = inGame && HighLogic.LoadedScene == GameScenes.FLIGHT ? FlightGlobals.ActiveVessel : null;
            if (active != null)
            {
                var c = FlightInputHandler.state;
                if (c != null && (c.mainThrottle > 0.001f || Math.Abs(c.pitch) > 0.01f || Math.Abs(c.yaw) > 0.01f
                                  || Math.Abs(c.roll) > 0.01f))
                {
                    long n;
                    activity.TryGetValue("controls", out n);
                    activity["controls"] = n + 1;
                }
            }
            j.Counts("actions", actions);
            j.Counts("activity", activity);
            j.Str("active_vessel", active != null ? active.id.ToString() : "");
            j.Key("vessels"); j.BeginList();
            if (inGame && FlightGlobals.Vessels != null)
                foreach (var v in FlightGlobals.Vessels)
                    if (v != null && v.mainBody != null) VesselJson(j, v);
            j.EndList();
            j.Key("roster"); j.BeginList();
            if (inGame && HighLogic.CurrentGame.CrewRoster != null)
                foreach (var k in HighLogic.CurrentGame.CrewRoster.Crew)
                {
                    j.Begin(); j.Str("name", k.name); j.Str("status", k.rosterStatus.ToString()); j.End();
                }
            j.EndList();
            j.End();
            string tmp = Path.Combine(dir, "state.json.tmp"), dst = Path.Combine(dir, "state.json");
            File.WriteAllText(tmp, j.ToString());
            // A reader may have state.json open for a moment (Windows then refuses to replace it):
            // retry briefly, and if it's still busy skip this sample; the next one comes in a second.
            for (int i = 0; ; i++)
            {
                try
                {
                    if (File.Exists(dst)) File.Replace(tmp, dst, null); else File.Move(tmp, dst);
                    break;
                }
                catch (IOException) when (i < 4) { System.Threading.Thread.Sleep(15); }
                catch (IOException) { return; }
            }
        }

        static void VesselJson(Json j, Vessel v)
        {
            var o = v.orbit;
            j.Begin();
            j.Str("id", v.id.ToString()); j.Str("name", v.vesselName); j.Str("type", v.vesselType.ToString());
            j.Str("situation", v.situation.ToString()); j.Str("body", v.mainBody.bodyName);
            j.Num("alt", v.altitude); j.Bool("loaded", v.loaded); j.Bool("packed", v.packed);
            if (o != null)
            {
                j.Num("pe", o.PeA); j.Num("ap", o.ApA);
                // Specific orbital energy (J/kg). A vessel on rails follows its orbit exactly, so its
                // energy is the orbit's; a loaded one's comes from its physics state.
                double mu = v.mainBody.gravParameter;
                double energy = v.loaded ? v.obt_speed * v.obt_speed / 2 - mu / (v.CoMD - v.mainBody.position).magnitude
                                         : -mu / (2 * o.semiMajorAxis);
                j.Num("speed", v.loaded ? v.obt_speed : o.orbitalSpeed);
                j.Num("energy", energy);
            }
            double accel = 0;
            if (v.loaded && v.parts != null)
            {
                double thrust = 0;
                foreach (var p in v.parts)
                {
                    foreach (var e in p.FindModulesImplementing<ModuleEngines>()) thrust += e.maxThrust;
                    foreach (var r in p.FindModulesImplementing<ModuleRCS>())
                        thrust += r.thrusterPower * (r.thrusterTransforms != null ? r.thrusterTransforms.Count : 1);
                }
                double mass = v.GetTotalMass();
                accel = mass > 0 ? thrust / mass : 0;
            }
            j.Num("max_accel", accel);
            j.Key("crew"); j.BeginList();
            foreach (var k in v.GetVesselCrew()) j.Item(k.name);
            j.EndList();
            j.End();
        }
    }

    // A small JSON writer (the game has no general one).
    class Json
    {
        readonly StringBuilder sb = new StringBuilder();
        bool first = true;

        void Sep() { if (!first) sb.Append(','); first = false; }
        public void Begin() { Sep(); sb.Append('{'); first = true; }
        public void End() { sb.Append('}'); first = false; }
        public void BeginList() { sb.Append('['); first = true; }
        public void EndList() { sb.Append(']'); first = false; }
        public void Key(string k) { Sep(); Q(k); sb.Append(':'); first = true; }
        public void Str(string k, string v) { Key(k); Q(v ?? ""); first = false; }
        public void Num(string k, double v) { Key(k); N(v); first = false; }
        public void Bool(string k, bool v) { Key(k); sb.Append(v ? "true" : "false"); first = false; }
        public void Item(string v) { Sep(); Q(v ?? ""); }
        public void Counts(string k, Dictionary<string, long> d)
        {
            Key(k); sb.Append('{'); first = true;
            foreach (var kv in d) { Key(kv.Key); sb.Append(kv.Value); first = false; }
            sb.Append('}'); first = false;
        }
        void N(double v)
        {
            if (double.IsNaN(v) || double.IsInfinity(v)) sb.Append("null");
            else sb.Append(v.ToString("R", CultureInfo.InvariantCulture));
        }
        void Q(string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            sb.Append('"');
        }
        public override string ToString() { return sb.ToString(); }
    }
}

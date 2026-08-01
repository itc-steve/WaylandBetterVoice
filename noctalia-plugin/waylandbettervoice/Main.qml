import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

Item {
  id: root

  property var pluginApi: null

  // Shared state — other entry points read via pluginApi.mainInstance
  property string mode: "offline"
  property real level: 0.0
  property string modelName: ""
  property bool modelLoaded: false
  property var meeting: ({
                           "active": false,
                           "file": null,
                           "speakers": 0,
                           "elapsed": 0
                         })
  property string lastText: ""
  property string error: ""
  property real since: 0
  property bool daemonUp: false
  property bool stateLoaded: false
  property real lastStateReadMs: 0

  readonly property var cfg: pluginApi?.pluginSettings || ({})
  readonly property var defaults: pluginApi?.manifest?.metadata?.defaultSettings || ({})
  readonly property real overlayTopMargin: cfg.overlayTopMargin ?? defaults.overlayTopMargin ?? 46
  readonly property real orbScale: cfg.orbScale ?? defaults.orbScale ?? 1.0
  readonly property bool showOrbWhileTranscribing: cfg.showOrbWhileTranscribing ?? defaults.showOrbWhileTranscribing ?? true
  readonly property bool meetingPillEnabled: cfg.meetingPillEnabled ?? defaults.meetingPillEnabled ?? true

  readonly property bool showOrbs: mode === "listening" || mode === "dictating" || (mode === "transcribing" && showOrbWhileTranscribing)
  readonly property bool showMeetingPill: mode === "meeting" && meetingPillEnabled
  readonly property bool overlayActive: showOrbs || showMeetingPill

  readonly property string statePath: {
    var runtime = Quickshell.env("XDG_RUNTIME_DIR") || "/tmp";
    return runtime + "/waylandbettervoice/state.json";
  }

  function applyOffline() {
    mode = "offline";
    level = 0.0;
    modelName = "";
    modelLoaded = false;
    meeting = {
      "active": false,
      "file": null,
      "speakers": 0,
      "elapsed": 0
    };
    lastText = "";
    error = "";
    since = 0;
    daemonUp = false;
    stateLoaded = false;
  }

  function applyState(obj) {
    if (!obj || typeof obj !== "object") {
      applyOffline();
      return;
    }
    mode = obj.mode || "idle";
    level = typeof obj.level === "number" ? obj.level : 0.0;
    modelName = obj.model || "";
    modelLoaded = !!obj.model_loaded;
    meeting = obj.meeting || {
      "active": false,
      "file": null,
      "speakers": 0,
      "elapsed": 0
    };
    lastText = obj.last_text || "";
    error = obj.error || "";
    since = typeof obj.since === "number" ? obj.since : 0;
    daemonUp = true;
  }

  function parseStateText(text) {
    if (!text || text.trim() === "")
      return false;
    try {
      applyState(JSON.parse(text));
      stateLoaded = true;
      lastStateReadMs = Date.now();
      return true;
    } catch (e) {
      // Keep last state: an incomplete read is not evidence daemon went offline.
      Logger.w("waylandbettervoice", "state.json parse failed:", e);
      return false;
    }
  }

  function runWbv(args) {
    // Fire-and-forget CLI; never touch the unix socket from QML.
    var cmd = ["wbv"].concat(args);
    wbvProcess.exec(cmd);
  }

  function dictate() {
    runWbv(["dictate", "toggle"]);
  }

  function meetingToggle() {
    runWbv(["meeting", "toggle"]);
  }

  function openMeetingsFolder() {
    var home = Quickshell.env("HOME") || "";
    openFolderProcess.exec(["xdg-open", home + "/.local/share/waylandbettervoice/meetings"]);
  }

  function copyLastText() {
    if (!lastText || lastText.length === 0)
      return;
    Quickshell.execDetached(["wl-copy", lastText]);
  }

  function formatElapsed(seconds) {
    var s = Math.max(0, Math.floor(Number(seconds) || 0));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r;
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    blockLoading: false
    preload: true

    onFileChanged: reload()
    onPathChanged: {
      if (path && path.length)
        reload();
    }
    onLoaded: root.parseStateText(text())
    onLoadFailed: function (error) {
      // Missing state.json is the only offline signal; stale state stays visible.
      root.applyOffline();
      Logger.d("waylandbettervoice", "state file missing/unreadable:", error);
    }
  }

  Timer {
    id: stateRecoveryTimer
    interval: 1000
    repeat: true
    running: true
    onTriggered: {
      // FileView does not watch a path created after shell start. Poll only until
      // first load, then once a healthy watch has been quiet for three seconds.
      if (!root.stateLoaded || Date.now() - root.lastStateReadMs > 3000)
        stateFile.reload();
    }
  }

  Process {
    id: wbvProcess
    // one-shot CLI runner; stdout ignored
  }

  Process {
    id: openFolderProcess
  }

  IpcHandler {
    target: "plugin:waylandbettervoice"

    function dictate() {
      root.dictate();
    }

    function meeting() {
      root.meetingToggle();
    }
  }

  // Overlay window. Instantiated once; the PanelWindow maps/unmaps itself via
  // `visible`, so there is no layer-shell surface while idle.
  // ponytail: no Loader — a Loader cannot host a Window, and LazyLoader would only
  // save a hidden window's memory.
  Orb {
    id: overlay
    pluginApi: root.pluginApi
    main: root
  }

  Component.onCompleted: {
    Logger.i("waylandbettervoice", "Main loaded, watching", root.statePath);
  }
}

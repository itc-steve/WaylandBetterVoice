import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Services.UI


Item {
  id: root

  property var pluginApi: null

  // Shared state — other entry points read via pluginApi.mainInstance
  property string mode: "offline"
  property real level: 0.0
  property string modelName: ""
  property bool modelLoaded: false
  property string injectMethod: "wtype"
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
  property string pendingInjectMethod: ""
  property var pendingPluginSettings: null

  // Keybinds actually configured for wbv, scraped from the live niri config.
  // niri has no IPC for binds, so the config tree is the only source of truth.
  // [{ keys: "Super+Space", action: "dictate.toggle" }]
  property var keybinds: []

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
    injectMethod = "wtype";
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
    injectMethod = obj.inject_method || "wtype";
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

  function setInjectMethod(method, settings) {
    if (injectModeProcess.running) {
      ToastService.showError("WaylandBetterVoice", "Injection mode change already in progress");
      return;
    }
    pendingInjectMethod = method;
    pendingPluginSettings = settings;
    // /bin/sh always starts, so missing wbv becomes exit 127 instead of a silent spawn failure.
    injectModeProcess.exec(["/bin/sh", "-c", "exec \"$@\"", "sh", "wbv", "inject", method]);
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
    id: injectModeProcess
    stderr: StdioCollector {
      id: injectModeError
    }
    onExited: function (exitCode) {
      if (exitCode !== 0) {
        const error = injectModeError.text.trim() || "wbv inject failed";
        Logger.e("waylandbettervoice", error);
        ToastService.showError("WaylandBetterVoice", error);
      } else {
        Object.assign(root.pluginApi.pluginSettings, root.pendingPluginSettings);
        root.pluginApi.saveSettings();
        root.injectMethod = root.pendingInjectMethod;
        Logger.i("waylandbettervoice", "Settings saved");
      }
      root.pendingInjectMethod = "";
      root.pendingPluginSettings = null;
    }
  }

  // Scrape `wbv` binds out of the niri config so the panel shows what is really
  // bound rather than what the docs assume. Read-only; failure just yields none.
  // ponytail: grep+sed, not a KDL parser — these binds are one line each by design
  Process {
    id: keybindScan
    command: ["sh", "-c", "grep -rhoE '^[[:space:]]*[A-Za-z0-9_+]+[[:space:]][^{]*\\{[[:space:]]*spawn(-sh)?[^}]*wbv[^}]*\\}' \"$HOME/.config/niri\" 2>/dev/null | sed -E 's/^[[:space:]]*([A-Za-z0-9_+]+).*wbv[[:space:]]+([a-z]+)[[:space:]]+([a-z]+).*/\\1\\t\\2.\\3/' | sed -E 's/^Mod\\+/Super+/; s/\\+ALT\\+/+Alt+/; s/\\+CTRL\\+/+Ctrl+/; s/\\+SHIFT\\+/+Shift+/' | sort -u"]
    running: false

    property var found: []

    stdout: SplitParser {
      onRead: line => {
        var parts = String(line).split("\t");
        if (parts.length === 2 && parts[0].trim() !== "")
          keybindScan.found.push({
            "keys": parts[0].trim(),
            "action": parts[1].trim()
          });
      }
    }

    onExited: {
      // shell sort -u orders by key name, which puts Meeting above Dictate.
      // Present them in the order a user thinks about them instead.
      var order = ["dictate.toggle", "dictate.start", "dictate.stop", "dictate.cancel", "meeting.toggle", "meeting.start", "meeting.stop"];
      found.sort(function (a, b) {
        var ia = order.indexOf(a.action);
        var ib = order.indexOf(b.action);
        if (ia < 0)
          ia = order.length;
        if (ib < 0)
          ib = order.length;
        return ia !== ib ? ia - ib : a.keys.localeCompare(b.keys);
      });
      root.keybinds = found;
      found = [];
    }
  }

  function refreshKeybinds() {
    if (keybindScan.running)
      return;
    keybindScan.found = [];
    keybindScan.running = true;
  }

  // Human label for an action id, e.g. "dictate.toggle" -> translated string.
  function keybindLabel(action) {
    var key = "keybind." + String(action).replace(".", "_");
    var s = pluginApi?.tr(key);
    return (s && s !== key) ? s : action;
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
    refreshKeybinds();
  }
}

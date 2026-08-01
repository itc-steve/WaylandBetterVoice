import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons

// Overlay window: thinking-orbs while dictating/transcribing, discreet pill while meeting.
// Reads live state from pluginApi.mainInstance (owned by Main.qml).
PanelWindow {
  id: root

  property var pluginApi: null

  // Set directly by Main.qml; falls back to the shell-provided instance.
  property var main: pluginApi?.mainInstance
  readonly property string mode: main?.mode ?? "offline"
  readonly property real level: main?.level ?? 0.0
  readonly property var meeting: main?.meeting ?? ({
                                                     "elapsed": 0
                                                   })
  readonly property real overlayTopMargin: main?.overlayTopMargin ?? 46
  readonly property real orbScale: main?.orbScale ?? 1.0
  readonly property bool showOrbs: main?.showOrbs ?? false
  readonly property bool showMeetingPill: main?.showMeetingPill ?? false

  // Base cluster size ~180x60, scaled.
  readonly property real baseW: 180 * orbScale
  readonly property real baseH: 60 * orbScale
  readonly property real pillW: 110
  readonly property real pillH: 26

  readonly property real contentW: showMeetingPill ? pillW : baseW
  readonly property real contentH: showMeetingPill ? pillH : baseH

  // Top-anchored, horizontally centered (only top set → compositor centers).
  anchors.top: true
  anchors.left: false
  anchors.right: false
  anchors.bottom: false
  margins.top: overlayTopMargin

  implicitWidth: Math.round(contentW)
  implicitHeight: Math.round(contentH)
  color: "transparent"
  visible: showOrbs || showMeetingPill

  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.namespace: "waylandbettervoice-overlay"
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
  exclusionMode: ExclusionMode.Ignore
  focusable: false

  // ---- Dictation / transcription orbs ----
  Item {
    id: orbCluster
    anchors.fill: parent
    visible: root.showOrbs
    opacity: root.mode === "transcribing" ? 0.7 : 1.0

    // Slow continuous rotation of the whole cluster.
    property real spin: 0
    NumberAnimation on spin {
      from: 0
      to: 360
      duration: root.mode === "transcribing" ? 14000 : 9000
      loops: Animation.Infinite
      running: orbCluster.visible
    }

    // Level-driven pulse (0.85–1.15).
    readonly property real pulse: 0.85 + 0.3 * Math.min(1.0, Math.max(0.0, root.level))

    // Three soft orbs orbiting a center point.
    // Spirit of thinking-orbs; original code. Soft look via layered translucent circles
    // (no MultiEffect blur required — cheaper, still reads as glow).
    Repeater {
      model: 3

      Item {
        id: orb
        required property int index

        readonly property real angle: (index * 120 + orbCluster.spin) * Math.PI / 180
        readonly property real orbitR: (root.baseW * 0.22) * (root.mode === "transcribing" ? 0.75 : 1.0)
        readonly property real cx: root.baseW / 2 + Math.cos(angle) * orbitR
        readonly property real cy: root.baseH / 2 + Math.sin(angle) * orbitR * 0.55
        readonly property real size: (root.baseH * 0.7) * orbCluster.pulse * (index === 0 ? 1.0 : index === 1 ? 0.85 : 0.7)

        // Palette: primary/secondary/tertiary; calmer (secondary-heavy) while transcribing.
        readonly property color coreColor: {
          if (root.mode === "transcribing") {
            if (index === 0)
              return Color.mSecondary;
            if (index === 1)
              return Color.mTertiary;
            return Color.mPrimary;
          }
          if (index === 0)
            return Color.mPrimary;
          if (index === 1)
            return Color.mSecondary;
          return Color.mTertiary;
        }

        x: cx - size / 2
        y: cy - size / 2
        width: size
        height: size

        // Outer soft halo
        Rectangle {
          anchors.centerIn: parent
          width: parent.width * 1.35
          height: parent.height * 1.35
          radius: width / 2
          color: Qt.alpha(orb.coreColor, root.mode === "transcribing" ? 0.12 : 0.18)
        }
        // Mid glow
        Rectangle {
          anchors.centerIn: parent
          width: parent.width * 1.1
          height: parent.height * 1.1
          radius: width / 2
          color: Qt.alpha(orb.coreColor, root.mode === "transcribing" ? 0.28 : 0.4)
        }
        // Core
        Rectangle {
          anchors.centerIn: parent
          width: parent.width * 0.7
          height: parent.height * 0.7
          radius: width / 2
          color: Qt.alpha(orb.coreColor, root.mode === "transcribing" ? 0.55 : 0.75)
        }
      }
    }
  }

  // ---- Meeting pill (screen-share safe) ----
  Rectangle {
    id: meetingPill
    anchors.centerIn: parent
    width: root.pillW
    height: root.pillH
    visible: root.showMeetingPill
    radius: height / 2
    color: Qt.alpha("#1a1a1a", 0.55)
    border.color: Qt.alpha("#ffffff", 0.08)
    border.width: 1
    opacity: 0.55

    Row {
      anchors.centerIn: parent
      spacing: 6

      // Dim red dot, 2s fade pulse.
      Rectangle {
        id: recDot
        width: 7
        height: 7
        radius: 3.5
        anchors.verticalCenter: parent.verticalCenter
        color: Qt.rgba(0.55, 0.12, 0.12, 1.0)

        SequentialAnimation on opacity {
          loops: Animation.Infinite
          running: meetingPill.visible
          NumberAnimation {
            from: 0.35
            to: 0.85
            duration: 1000
            easing.type: Easing.InOutSine
          }
          NumberAnimation {
            from: 0.85
            to: 0.35
            duration: 1000
            easing.type: Easing.InOutSine
          }
        }
      }

      Text {
        // Raw Text — NText pulls theme fonts that read loud on a screen-share pill.
        // ponytail: raw Text for discreet share-safe timer, NText if pill ever leaves overlay
        anchors.verticalCenter: parent.verticalCenter
        text: {
          var elapsed = root.meeting && root.meeting.elapsed !== undefined ? root.meeting.elapsed : 0;
          if (root.main && root.main.formatElapsed)
            return root.main.formatElapsed(elapsed);
          var s = Math.max(0, Math.floor(Number(elapsed) || 0));
          var m = Math.floor(s / 60);
          var r = s % 60;
          return (m < 10 ? "0" : "") + m + ":" + (r < 10 ? "0" : "") + r;
        }
        color: Qt.rgba(0.75, 0.75, 0.75, 0.9)
        font.pixelSize: 11
        font.family: "monospace"
      }
    }
  }
}

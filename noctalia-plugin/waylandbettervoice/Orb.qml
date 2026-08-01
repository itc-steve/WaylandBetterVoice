import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons

// Overlay window: a compact status capsule while listening/dictating/transcribing,
// a discreet pill while recording a meeting.
//
// Design notes: the first version floated three large blurred circles directly over
// the desktop. Over text it read as random bokeh — no shape, no edge, unclear what it
// meant. This version keeps everything inside one small dark capsule with a defined
// border, so it reads as a deliberate UI element instead of a rendering artifact.
PanelWindow {
  id: root

  property var pluginApi: null
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

  // Compact capsule. Small enough to ignore, big enough to read at a glance.
  readonly property real capsuleW: Math.round(132 * orbScale)
  readonly property real capsuleH: Math.round(34 * orbScale)
  readonly property real pillW: 110
  readonly property real pillH: 26

  anchors.top: true
  anchors.left: false
  anchors.right: false
  anchors.bottom: false
  margins.top: overlayTopMargin
  implicitWidth: Math.round(showMeetingPill ? pillW : capsuleW)
  implicitHeight: Math.round(showMeetingPill ? pillH : capsuleH)
  color: "transparent"
  visible: showOrbs || showMeetingPill

  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.namespace: "waylandbettervoice-overlay"
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
  exclusionMode: ExclusionMode.Ignore
  focusable: false

  // ---- Dictation capsule ----
  Rectangle {
    id: capsule
    anchors.centerIn: parent
    width: root.capsuleW
    height: root.capsuleH
    visible: root.showOrbs
    radius: height / 2
    // Solid dark backing: the dots must never sit directly on top of body text.
    color: Qt.alpha(Color.mSurface, 0.92)
    border.width: 1
    border.color: Qt.alpha(accentColor, root.mode === "listening" ? 0.35 : 0.55)

    readonly property color accentColor: {
      if (root.mode === "transcribing")
        return Color.mTertiary;
      if (root.mode === "dictating")
        return Color.mPrimary;
      return Color.mSecondary;      // listening
    }

    // Smoothed level: fast attack so speech feels responsive, slow release so it
    // does not flicker between syllables.
    property real responseLevel: root.mode === "dictating" ? Math.min(1.0, Math.max(0.0, root.level)) : 0.0
    Behavior on responseLevel {
      NumberAnimation {
        duration: 90
        easing.type: Easing.OutQuad
      }
    }

    opacity: 0.0
    Component.onCompleted: opacity = 1.0
    Behavior on opacity {
      NumberAnimation {
        duration: 140
        easing.type: Easing.OutQuad
      }
    }

    Row {
      anchors.centerIn: parent
      spacing: Math.round(7 * root.orbScale)

      // Three dots. Small, sharp, on a baseline — a recognizable "thinking" motif.
      Repeater {
        model: 3

        Item {
          id: cell
          required property int index

          readonly property real baseSize: Math.round(7 * root.orbScale)
          // In dictating mode each dot also responds to mic level, staggered so the
          // group ripples instead of pulsing as one block.
          readonly property real levelBoost: capsule.responseLevel * (index === 1 ? 1.0 : 0.7)

          width: baseSize * 2.0
          height: baseSize * 2.0
          anchors.verticalCenter: parent.verticalCenter

          // Soft halo, drawn as a real circle rather than a blur pass. Keeps the
          // edge crisp and costs nothing on the GPU.
          Rectangle {
            anchors.centerIn: parent
            width: dot.width * 2.1
            height: width
            radius: width / 2
            color: Qt.alpha(capsule.accentColor, 0.16)
            opacity: dot.opacity
          }

          Rectangle {
            id: dot
            anchors.centerIn: parent
            width: cell.baseSize * (0.8 + 0.35 * cell.levelBoost)
            height: width
            radius: width / 2
            color: capsule.accentColor

            SequentialAnimation on opacity {
              running: capsule.visible
              loops: Animation.Infinite
              // Staggered wave: dot 0 leads, 1 follows, 2 trails.
              PauseAnimation {
                duration: cell.index * 160
              }
              NumberAnimation {
                from: 0.3
                to: 1.0
                duration: root.mode === "listening" ? 620 : 380
                easing.type: Easing.InOutSine
              }
              NumberAnimation {
                from: 1.0
                to: 0.3
                duration: root.mode === "listening" ? 620 : 380
                easing.type: Easing.InOutSine
              }
              PauseAnimation {
                duration: (2 - cell.index) * 160
              }
            }

            Behavior on width {
              NumberAnimation {
                duration: 90
                easing.type: Easing.OutQuad
              }
            }
          }
        }
      }
    }
  }

  // ---- Meeting pill (screen-share safe: dim, tiny, minimal motion) ----
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
        anchors.verticalCenter: parent.verticalCenter
        // ponytail: raw Text — NText pulls theme fonts that read loud on a shared screen
        text: {
          var elapsed = root.meeting && root.meeting.elapsed !== undefined ? root.meeting.elapsed : 0;
          return root.main?.formatElapsed ? root.main.formatElapsed(elapsed) : "00:00";
        }
        color: Qt.rgba(0.75, 0.75, 0.75, 0.9)
        font.pixelSize: 11
        font.family: "monospace"
      }
    }
  }
}

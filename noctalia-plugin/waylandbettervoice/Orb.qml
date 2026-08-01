import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Wayland
import qs.Commons

// Overlay window: glowing thinking-orbs while listening/dictating/transcribing,
// discreet pill while meeting.
PanelWindow {
  id: root

  property var pluginApi: null
  property var main: pluginApi?.mainInstance
  readonly property string mode: main?.mode ?? "offline"
  readonly property real level: main?.level ?? 0.0
  readonly property var meeting: main?.meeting ?? ({ "elapsed": 0 })
  readonly property real overlayTopMargin: main?.overlayTopMargin ?? 46
  readonly property real orbScale: main?.orbScale ?? 1.0
  readonly property bool showOrbs: main?.showOrbs ?? false
  readonly property bool showMeetingPill: main?.showMeetingPill ?? false

  readonly property real baseW: 180 * orbScale
  readonly property real baseH: 60 * orbScale
  readonly property real pillW: 110
  readonly property real pillH: 26
  readonly property real contentW: showMeetingPill ? pillW : baseW
  readonly property real contentH: showMeetingPill ? pillH : baseH

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

  Item {
    id: orbCluster
    anchors.fill: parent
    visible: root.showOrbs
    opacity: root.mode === "listening" ? 0.48 : root.mode === "transcribing" ? 0.7 : 1.0
    property real responseLevel: Math.min(1.0, Math.max(0.0, root.level))
    readonly property real pulse: root.mode === "dictating" ? 0.92 + 0.28 * responseLevel : root.mode === "listening" ? 0.72 : 0.84

    // Fast attack, slow release keeps speech response lively without jitter.
    Behavior on responseLevel {
      NumberAnimation {
        duration: root.level > orbCluster.responseLevel ? 110 : 480
        easing.type: root.level > orbCluster.responseLevel ? Easing.OutCubic : Easing.InOutSine
      }
    }

    Repeater {
      model: 3

      Item {
        id: orb
        required property int index
        property real phase: index * 120
        readonly property real angle: phase * Math.PI / 180
        readonly property real orbitR: root.baseW * (root.mode === "listening" ? 0.15 : root.mode === "transcribing" ? 0.18 : 0.22) * (1.0 + 0.08 * Math.sin(angle * 1.7 + index))
        readonly property real size: root.baseH * (root.mode === "listening" ? 0.46 : 0.7) * orbCluster.pulse * (index === 0 ? 1.0 : index === 1 ? 0.86 : 0.72)
        readonly property color coreColor: {
          if (root.mode === "transcribing")
            return index === 0 ? Color.mSecondary : index === 1 ? Color.mTertiary : Color.mPrimary;
          return index === 0 ? Color.mPrimary : index === 1 ? Color.mSecondary : Color.mTertiary;
        }

        x: root.baseW / 2 + Math.cos(angle) * orbitR - width / 2
        y: root.baseH / 2 + Math.sin(angle) * orbitR * 0.55 - height / 2
        width: size
        height: size

        NumberAnimation on phase {
          from: index * 120
          to: index * 120 + 360
          duration: root.mode === "listening" ? 18000 + index * 1700 : root.mode === "transcribing" ? 14000 + index * 1300 : 7200 + index * 900
          loops: Animation.Infinite
          running: orbCluster.visible
        }

        Item {
          id: orbArt
          anchors.fill: parent
          Rectangle {
            anchors.centerIn: parent
            width: parent.width * 1.28
            height: parent.height * 1.28
            radius: width / 2
            color: Qt.alpha(orb.coreColor, root.mode === "listening" ? 0.12 : 0.2)
          }
          Rectangle {
            anchors.centerIn: parent
            width: parent.width
            height: parent.height
            radius: width / 2
            color: Qt.alpha(orb.coreColor, root.mode === "transcribing" ? 0.4 : 0.58)
          }
          Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.62
            height: parent.height * 0.62
            radius: width / 2
            color: Qt.alpha(orb.coreColor, root.mode === "listening" ? 0.52 : 0.9)
          }
        }

        // Installed Qt 6.5+ MultiEffect gives layered circles a real soft halo.
        MultiEffect {
          anchors.centerIn: parent
          width: orbArt.width * 1.8
          height: orbArt.height * 1.8
          source: orbArt
          blurEnabled: true
          blur: 0.72
          blurMax: 32
          opacity: root.mode === "listening" ? 0.36 : root.mode === "transcribing" ? 0.52 : 0.76
          z: -1
        }
      }
    }
  }

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
          NumberAnimation { from: 0.35; to: 0.85; duration: 1000; easing.type: Easing.InOutSine }
          NumberAnimation { from: 0.85; to: 0.35; duration: 1000; easing.type: Easing.InOutSine }
        }
      }
      Text {
        anchors.verticalCenter: parent.verticalCenter
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

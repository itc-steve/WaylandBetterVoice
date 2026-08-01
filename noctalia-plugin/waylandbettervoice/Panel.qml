import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Widgets

Item {
  id: root
  property var pluginApi: null

  readonly property var geometryPlaceholder: panelContainer
  readonly property bool allowAttach: true
  property real contentPreferredWidth: 380 * Style.uiScaleRatio
  property real contentPreferredHeight: 420 * Style.uiScaleRatio

  readonly property var main: pluginApi?.mainInstance
  readonly property string mode: main?.mode ?? "offline"
  readonly property string modelName: main?.modelName ?? "—"
  readonly property bool daemonUp: main?.daemonUp ?? false
  readonly property bool modelLoaded: main?.modelLoaded ?? false
  readonly property string lastText: main?.lastText ?? ""
  readonly property string errorText: main?.error ?? ""
  readonly property var meeting: main?.meeting ?? ({
                                                     "active": false,
                                                     "elapsed": 0
                                                   })
  readonly property bool meetingActive: !!(meeting && meeting.active) || mode === "meeting"
  readonly property string elapsedLabel: {
    var e = meeting && meeting.elapsed !== undefined ? meeting.elapsed : 0;
    if (main && main.formatElapsed)
      return main.formatElapsed(e);
    return "00:00";
  }

  anchors.fill: parent

  function runWbv(args) {
    if (main && args[0] === "dictate" && main.dictate && args[1] === "toggle") {
      main.dictate();
      return;
    }
    if (main && args[0] === "meeting" && main.meetingToggle && args[1] === "toggle") {
      main.meetingToggle();
      return;
    }
    wbvProc.exec(["wbv"].concat(args));
  }

  Rectangle {
    id: panelContainer
    anchors.fill: parent
    color: "transparent"

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Style.marginL
      spacing: Style.marginM

      NHeader {
        label: pluginApi?.tr("panel.title")
        description: pluginApi?.tr("mode." + root.mode) || root.mode
      }

      // Status rows
      NBox {
        Layout.fillWidth: true
        Layout.preferredHeight: statusCol.implicitHeight + Style.marginM * 2

        ColumnLayout {
          id: statusCol
          anchors.fill: parent
          anchors.margins: Style.marginM
          spacing: Style.marginS

          RowLayout {
            Layout.fillWidth: true
            NText {
              text: pluginApi?.tr("panel.daemon")
              color: Color.mOnSurfaceVariant
              pointSize: Style.fontSizeS
            }
            Item {
              Layout.fillWidth: true
            }
            NText {
              text: root.daemonUp ? pluginApi?.tr("panel.daemon_up") : pluginApi?.tr("panel.daemon_down")
              color: root.daemonUp ? Color.mPrimary : Color.mError
              pointSize: Style.fontSizeS
              font.weight: Style.fontWeightSemiBold
            }
          }

          RowLayout {
            Layout.fillWidth: true
            NText {
              text: pluginApi?.tr("panel.model")
              color: Color.mOnSurfaceVariant
              pointSize: Style.fontSizeS
            }
            Item {
              Layout.fillWidth: true
            }
            NText {
              text: (root.modelName && root.modelName.length) ? root.modelName : "—"
              color: root.modelLoaded ? Color.mOnSurface : Color.mOnSurfaceVariant
              pointSize: Style.fontSizeS
              elide: Text.ElideMiddle
              Layout.maximumWidth: 200 * Style.uiScaleRatio
              horizontalAlignment: Text.AlignRight
            }
          }

          RowLayout {
            Layout.fillWidth: true
            NText {
              text: pluginApi?.tr("panel.mode")
              color: Color.mOnSurfaceVariant
              pointSize: Style.fontSizeS
            }
            Item {
              Layout.fillWidth: true
            }
            NText {
              text: pluginApi?.tr("mode." + root.mode) || root.mode
              color: Color.mOnSurface
              pointSize: Style.fontSizeS
              font.weight: Style.fontWeightSemiBold
            }
          }
        }
      }

      // Last transcript
      NLabel {
        label: pluginApi?.tr("panel.last_text")
      }

      NBox {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 80 * Style.uiScaleRatio

        // Selectable multi-line transcript. No N* multi-line selectable widget.
        // ponytail: TextEdit for select/copy, swap if Noctalia adds NTextArea
        Flickable {
          id: textFlick
          anchors.fill: parent
          anchors.margins: Style.marginM
          contentWidth: width
          contentHeight: transcriptEdit.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick

          TextEdit {
            id: transcriptEdit
            width: textFlick.width
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            color: Color.mOnSurface
            selectedTextColor: Color.mOnPrimary
            selectionColor: Color.mPrimary
            font.family: Settings.data.ui.fontDefault
            font.pointSize: Style.fontSizeS
            text: root.lastText && root.lastText.length ? root.lastText : (pluginApi?.tr("panel.last_text_empty") || "")
          }
        }
      }

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.marginS

        NButton {
          text: pluginApi?.tr("panel.copy")
          icon: "copy"
          enabled: root.lastText && root.lastText.length > 0
          onClicked: {
            if (main && main.copyLastText)
              main.copyLastText();
            else if (root.lastText && root.lastText.length)
              Quickshell.execDetached(["wl-copy", root.lastText]);
          }
        }

        Item {
          Layout.fillWidth: true
        }

        NButton {
          text: root.meetingActive ? pluginApi?.tr("panel.meeting_stop") : pluginApi?.tr("panel.meeting_start")
          icon: root.meetingActive ? "player-stop" : "player-record"
          backgroundColor: root.meetingActive ? Color.mError : Color.mPrimary
          onClicked: root.runWbv(["meeting", "toggle"])
        }
      }

      RowLayout {
        Layout.fillWidth: true
        visible: root.meetingActive
        NText {
          text: pluginApi?.tr("panel.meeting_elapsed")
          color: Color.mOnSurfaceVariant
          pointSize: Style.fontSizeS
        }
        NText {
          text: root.elapsedLabel
          color: Color.mError
          pointSize: Style.fontSizeS
          font.weight: Style.fontWeightSemiBold
        }
        Item {
          Layout.fillWidth: true
        }
      }

      NButton {
        Layout.fillWidth: true
        text: pluginApi?.tr("panel.open_meetings")
        icon: "folder"
        outlined: true
        onClicked: {
          if (main && main.openMeetingsFolder)
            main.openMeetingsFolder();
          else {
            var home = Quickshell.env("HOME") || "";
            openFolderProc.exec(["xdg-open", home + "/.local/share/waylandbettervoice/meetings"]);
          }
        }
      }

      // Error row
      NBox {
        Layout.fillWidth: true
        Layout.preferredHeight: errorCol.implicitHeight + Style.marginM * 2
        visible: root.errorText && root.errorText.length > 0

        ColumnLayout {
          id: errorCol
          anchors.fill: parent
          anchors.margins: Style.marginM
          spacing: Style.marginXS

          NText {
            text: pluginApi?.tr("panel.error")
            color: Color.mError
            pointSize: Style.fontSizeS
            font.weight: Style.fontWeightSemiBold
          }
          NText {
            Layout.fillWidth: true
            text: root.errorText
            color: Color.mOnSurface
            pointSize: Style.fontSizeS
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }

  Process {
    id: wbvProc
  }
  Process {
    id: openFolderProc
  }
}

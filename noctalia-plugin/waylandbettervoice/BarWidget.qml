import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Services.UI
import qs.Widgets

Item {
  id: root

  property var pluginApi: null
  property ShellScreen screen
  property string widgetId: ""
  property string section: ""
  property int sectionWidgetIndex: -1
  property int sectionWidgetsCount: 0

  property var cfg: pluginApi?.pluginSettings || ({})
  property var defaults: pluginApi?.manifest?.metadata?.defaultSettings || ({})

  readonly property string barPosition: Settings.getBarPositionForScreen(screen?.name)
  readonly property bool isVertical: barPosition === "left" || barPosition === "right"
  readonly property real capsuleHeight: Style.getCapsuleHeightForScreen(screen?.name)

  readonly property var main: pluginApi?.mainInstance
  readonly property string mode: main?.mode ?? "offline"
  readonly property string modelName: main?.modelName ?? ""
  readonly property bool meetingActive: !!(main?.meeting && main.meeting.active) || mode === "meeting"

  readonly property string iconColorKey: cfg.iconColor ?? defaults.iconColor ?? "none"

  // Tint by mode; idle falls back to configured icon color.
  readonly property color modeColor: {
    if (mode === "dictating")
      return Color.mPrimary;
    if (mode === "transcribing")
      return Color.mSecondary;
    if (mode === "meeting")
      return Color.mError;
    if (mode === "error")
      return Color.mError;
    if (mode === "offline")
      return Color.mOnSurfaceVariant;
    return Color.resolveColorKey(iconColorKey);
  }

  readonly property string tooltipText: {
    if (mode === "offline")
      return pluginApi?.tr("widget.tooltip_offline");
    var modeLabel = pluginApi?.tr("mode." + mode) || mode;
    var modelLabel = modelName && modelName.length ? modelName : "—";
    return pluginApi?.tr("widget.tooltip", {
      "mode": modeLabel,
      "model": modelLabel
    });
  }

  implicitWidth: isVertical ? capsuleHeight : capsuleHeight
  implicitHeight: isVertical ? capsuleHeight : capsuleHeight

  NIconButton {
    id: btn
    anchors.centerIn: parent
    baseSize: root.capsuleHeight
    applyUiScale: false
    icon: root.mode === "offline" || root.mode === "error" ? "microphone-off" : "microphone"
    tooltipText: root.tooltipText
    tooltipDirection: BarService.getTooltipDirection(root.screen?.name)
    customRadius: Style.radiusL

    colorBg: Style.capsuleColor
    colorFg: root.modeColor
    colorBgHover: Color.mHover
    colorFgHover: Color.mOnHover
    colorBorder: Style.capsuleBorderColor
    colorBorderHover: Style.capsuleBorderColor

    onClicked: {
      if (root.main && root.main.dictate)
        root.main.dictate();
      else
        wbvProc.exec(["wbv", "dictate", "toggle"]);
    }

    onRightClicked: {
      PanelService.showContextMenu(contextMenu, root, root.screen);
    }
  }

  NPopupContextMenu {
    id: contextMenu
    model: [
      {
        "label": pluginApi?.tr("menu.open_panel"),
        "action": "panel",
        "icon": "layout-sidebar"
      },
      {
        "label": root.meetingActive ? pluginApi?.tr("menu.meeting_stop") : pluginApi?.tr("menu.meeting_start"),
        "action": "meeting",
        "icon": "player-record"
      },
      {
        "label": pluginApi?.tr("menu.settings"),
        "action": "settings",
        "icon": "settings"
      }
    ]
    onTriggered: action => {
      contextMenu.close();
      PanelService.closeContextMenu(root.screen);
      if (action === "panel") {
        if (pluginApi)
          pluginApi.openPanel(root.screen, root);
      } else if (action === "meeting") {
        if (root.main && root.main.meetingToggle)
          root.main.meetingToggle();
        else
          wbvProc.exec(["wbv", "meeting", "toggle"]);
      } else if (action === "settings") {
        if (pluginApi)
          BarService.openPluginSettings(root.screen, pluginApi.manifest);
      }
    }
  }

  Process {
    id: wbvProc
  }
}

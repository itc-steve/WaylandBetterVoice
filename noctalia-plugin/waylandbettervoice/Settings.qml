import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Widgets

ColumnLayout {
  id: root
  property var pluginApi: null

  property var cfg: pluginApi?.pluginSettings || ({})
  property var defaults: pluginApi?.manifest?.metadata?.defaultSettings || ({})
  property var main: pluginApi?.mainInstance

  // Edit copies — never write pluginSettings from bindings.
  property real editOverlayTopMargin: cfg.overlayTopMargin ?? defaults.overlayTopMargin ?? 46
  property real editOrbScale: cfg.orbScale ?? defaults.orbScale ?? 1.0
  property bool editShowOrbWhileTranscribing: cfg.showOrbWhileTranscribing ?? defaults.showOrbWhileTranscribing ?? true
  property bool editMeetingPillEnabled: cfg.meetingPillEnabled ?? defaults.meetingPillEnabled ?? true
  property string editIconColor: cfg.iconColor ?? defaults.iconColor ?? "none"
  property bool editElectronCompatibility: main?.daemonUp
      ? main.injectMethod === "ydotool"
      : cfg.electronCompatibility ?? defaults.electronCompatibility ?? false
  property bool electronCompatibilityDirty: false

  spacing: Style.marginL

  NToggle {
    Layout.fillWidth: true
    label: pluginApi?.tr("settings.electron_mode_label")
    description: pluginApi?.tr("settings.electron_mode_desc")
    checked: root.editElectronCompatibility
    onToggled: checked => {
      root.editElectronCompatibility = checked;
      root.electronCompatibilityDirty = true;
    }
  }

  NSpinBox {
    Layout.fillWidth: true
    label: pluginApi?.tr("settings.overlay_margin_label")
    description: pluginApi?.tr("settings.overlay_margin_desc")
    from: 0
    to: 200
    stepSize: 1
    value: Math.round(root.editOverlayTopMargin)
    suffix: " px"
    onValueChanged: root.editOverlayTopMargin = value
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.marginS

    NLabel {
      label: pluginApi?.tr("settings.orb_scale_label")
      description: pluginApi?.tr("settings.orb_scale_desc")
    }

    NValueSlider {
      Layout.fillWidth: true
      from: 0.5
      to: 2.0
      stepSize: 0.05
      value: root.editOrbScale
      text: (Math.round(root.editOrbScale * 100) / 100).toFixed(2) + "×"
      onMoved: v => root.editOrbScale = v
    }
  }

  NToggle {
    Layout.fillWidth: true
    label: pluginApi?.tr("settings.show_transcribing_label")
    description: pluginApi?.tr("settings.show_transcribing_desc")
    checked: root.editShowOrbWhileTranscribing
    onToggled: checked => root.editShowOrbWhileTranscribing = checked
  }

  NToggle {
    Layout.fillWidth: true
    label: pluginApi?.tr("settings.meeting_pill_label")
    description: pluginApi?.tr("settings.meeting_pill_desc")
    checked: root.editMeetingPillEnabled
    onToggled: checked => root.editMeetingPillEnabled = checked
  }

  NColorChoice {
    Layout.fillWidth: true
    label: pluginApi?.tr("settings.icon_color_label")
    description: pluginApi?.tr("settings.icon_color_desc")
    currentKey: root.editIconColor
    onSelected: key => root.editIconColor = key
  }

  function saveSettings() {
    if (!pluginApi) {
      Logger.e("waylandbettervoice", "Cannot save settings: pluginApi is null");
      return;
    }
    const settings = {
      "overlayTopMargin": Math.round(root.editOverlayTopMargin),
      "orbScale": root.editOrbScale,
      "showOrbWhileTranscribing": root.editShowOrbWhileTranscribing,
      "meetingPillEnabled": root.editMeetingPillEnabled,
      "iconColor": root.editIconColor,
      "electronCompatibility": root.editElectronCompatibility
    };
    if (root.electronCompatibilityDirty) {
      if (root.main?.setInjectMethod)
        root.main.setInjectMethod(root.editElectronCompatibility ? "ydotool" : "wtype", settings);
      else
        Logger.e("waylandbettervoice", "Cannot change injection mode: main instance unavailable");
      return;
    }
    Object.assign(pluginApi.pluginSettings, settings);
    pluginApi.saveSettings();
    Logger.i("waylandbettervoice", "Settings saved");
  }
}

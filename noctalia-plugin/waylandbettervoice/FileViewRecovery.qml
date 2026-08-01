import QtQuick
import Quickshell
import Quickshell.Io

// Run with qs -p FileViewRecovery.qml after removing the target file.
ShellRoot {
  id: root
  property bool recovered: false
  property string path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/wbv-fileview-recovery.json"

  FileView {
    id: watchedFile
    path: root.path
    watchChanges: true
    printErrors: false
    onLoaded: {
      if (text().trim() === "recovered") {
        root.recovered = true;
        console.log("RECOVERED", text().trim());
      }
    }
    onLoadFailed: console.log("WAITING")
  }

  Timer {
    interval: 1000
    repeat: true
    running: true
    onTriggered: {
      if (!root.recovered) {
        console.log("RELOAD")
        watchedFile.reload()
      }
    }
  }
}

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getDesktopSources: ()              => ipcRenderer.invoke('get-desktop-sources'),
  saveRecording:     (buffer, name)  => ipcRenderer.invoke('save-recording', buffer, name),
});

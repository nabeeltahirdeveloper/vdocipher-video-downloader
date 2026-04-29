const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getVideoConfig:    ()               => ipcRenderer.invoke('get-video-config'),
  getDesktopSources: ()               => ipcRenderer.invoke('get-desktop-sources'),
  saveRecording:     (buffer, name)   => ipcRenderer.invoke('save-recording', buffer, name),
});

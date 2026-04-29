const { app, BrowserWindow, ipcMain, desktopCapturer, session } = require('electron');
const path = require('path');
const fs = require('fs');

// ---------------------------------------------------------------------------
// Parse argv:  --otp X  --playback-info Y  [--record]  [--output DIR]
// ---------------------------------------------------------------------------
const argv = process.argv.slice(2);
const getArg  = (name) => { const i = argv.indexOf(name); return i !== -1 ? argv[i + 1] : null; };
const hasFlag = (name) => argv.includes(name);

const OTP           = getArg('--otp');
const PLAYBACK_INFO = getArg('--playback-info');
const OUTPUT_DIR    = getArg('--output') || '.';
const RECORD_MODE   = hasFlag('--record');

if (!OTP || !PLAYBACK_INFO) {
  console.error('Usage: electron . --otp <otp> --playback-info <info> [--record] [--output <dir>]');
  process.exit(1);
}

const PLAYER_URL =
  `https://player.vdocipher.com/v2/?otp=${OTP}&playbackInfo=${PLAYBACK_INFO}`;

// ---------------------------------------------------------------------------
// Widevine CDM – load from system Chrome on macOS
// ---------------------------------------------------------------------------
function findChromeWidevineCDM() {
  if (process.platform !== 'darwin') return null;

  const arch = process.arch === 'arm64' ? 'mac_arm64' : 'mac_x64';
  const browsers = [
    'Google Chrome', 'Google Chrome Beta', 'Chromium',
    'Brave Browser', 'Microsoft Edge',
  ];

  for (const browser of browsers) {
    const base = `/Applications/${browser}.app/Contents/Frameworks`;
    let frameworkDir;
    try { frameworkDir = fs.readdirSync(base).find(d => d.endsWith('.framework')); }
    catch (_) { continue; }
    if (!frameworkDir) continue;

    const cdmPath = path.join(
      base, frameworkDir,
      'Versions', 'Current', 'Libraries',
      'WidevineCdm', '_platform_specific', arch,
      'libwidevinecdm.dylib'
    );
    if (fs.existsSync(cdmPath)) return cdmPath;
  }
  return null;
}

function readCDMVersion(cdmPath) {
  try {
    const manifest = path.join(path.dirname(cdmPath), '..', '..', 'manifest.json');
    return JSON.parse(fs.readFileSync(manifest, 'utf8')).version || '4.10.2710.0';
  } catch (_) { return '4.10.2710.0'; }
}

const cdmPath = findChromeWidevineCDM();
if (cdmPath) {
  const version = readCDMVersion(cdmPath);
  console.log(`Widevine CDM: ${cdmPath} (v${version})`);
  app.commandLine.appendSwitch('widevine-cdm-path', cdmPath);
  app.commandLine.appendSwitch('widevine-cdm-version', version);
} else {
  console.warn('Widevine CDM not found – install Google Chrome for DRM support.');
}

// ---------------------------------------------------------------------------
// Recording toolbar injected into the VDO Cipher player page
// ---------------------------------------------------------------------------
const TOOLBAR_JS = `
(function () {
  if (document.getElementById('__vdo_rec_toolbar')) return;

  const bar = document.createElement('div');
  bar.id = '__vdo_rec_toolbar';
  bar.style.cssText = [
    'position:fixed', 'bottom:0', 'left:0', 'right:0', 'z-index:99999',
    'background:rgba(17,17,17,0.92)', 'backdrop-filter:blur(6px)',
    'display:flex', 'align-items:center', 'gap:10px',
    'padding:8px 14px', 'font:13px/1 -apple-system,BlinkMacSystemFont,sans-serif',
    'color:#fff', 'border-top:1px solid #333',
  ].join(';');

  bar.innerHTML = \`
    <span id="__vdo_status" style="flex:1;color:#999;font-size:12px">Ready to record</span>
    <button id="__vdo_rec_btn"
      style="background:#c62828;color:#fff;border:none;border-radius:4px;
             padding:5px 14px;font-size:12px;cursor:pointer">
      ⏺ Record
    </button>
    <button id="__vdo_stop_btn"
      style="background:#37474f;color:#fff;border:none;border-radius:4px;
             padding:5px 14px;font-size:12px;cursor:pointer;display:none">
      ⏹ Stop &amp; Save
    </button>
  \`;
  document.body.appendChild(bar);

  let recorder = null;
  let chunks   = [];

  const status  = () => document.getElementById('__vdo_status');
  const recBtn  = () => document.getElementById('__vdo_rec_btn');
  const stopBtn = () => document.getElementById('__vdo_stop_btn');

  recBtn().addEventListener('click', async () => {
    try {
      status().textContent = 'Requesting capture permission…';
      const sources = await window.electronAPI.getDesktopSources();
      const src =
        sources.find(s => s.name === 'VDO Cipher Player') ||
        sources.find(s => /screen/i.test(s.name)) ||
        sources[0];

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: 'desktop' } },
        video: {
          mandatory: {
            chromeMediaSource: 'desktop',
            chromeMediaSourceId: src.id,
            minWidth: 1280, maxWidth: 1920,
            minHeight: 720, maxHeight: 1080,
          },
        },
      });

      chunks  = [];
      const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus')
        ? 'video/webm;codecs=vp9,opus' : 'video/webm';
      recorder = new MediaRecorder(stream, { mimeType: mime });
      recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
      recorder.start(1000);

      recBtn().innerHTML  = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#f44;margin-right:6px;animation:blink 1s step-start infinite"></span>Recording';
      recBtn().disabled   = true;
      stopBtn().style.display = 'inline-block';
      status().textContent = 'Recording…';

      if (!document.getElementById('__vdo_blink_style')) {
        const s = document.createElement('style');
        s.id = '__vdo_blink_style';
        s.textContent = '@keyframes blink{50%{opacity:0}}';
        document.head.appendChild(s);
      }
    } catch (err) {
      status().textContent = 'Error: ' + err.message;
    }
  });

  stopBtn().addEventListener('click', () => {
    if (!recorder || recorder.state === 'inactive') return;
    stopBtn().disabled = true;
    status().textContent = 'Saving…';

    recorder.onstop = async () => {
      try {
        const blob   = new Blob(chunks, { type: 'video/webm' });
        const ab     = await blob.arrayBuffer();
        const buf    = Array.from(new Uint8Array(ab));
        const ts     = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
        const saved  = await window.electronAPI.saveRecording(buf, 'recording-' + ts + '.webm');
        status().textContent = 'Saved → ' + saved;
      } catch (err) {
        status().textContent = 'Save error: ' + err.message;
      } finally {
        recorder.stream.getTracks().forEach(t => t.stop());
        recBtn().innerHTML = '⏺ Record';
        recBtn().disabled  = false;
        stopBtn().style.display = 'none';
        stopBtn().disabled = false;
      }
    };
    recorder.stop();
  });
})();
`;

// ---------------------------------------------------------------------------
// App bootstrap
// ---------------------------------------------------------------------------
app.whenReady().then(() => {
  // Allow VDO Cipher player to load mixed / cross-origin resources
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    // Remove restrictive CSP that would block our injected toolbar
    delete headers['content-security-policy'];
    delete headers['Content-Security-Policy'];
    callback({ responseHeaders: headers });
  });

  const win = new BrowserWindow({
    width: 1280,
    height: 760,
    title: 'VDO Cipher Player',
    backgroundColor: '#000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  console.log(`Loading player: ${PLAYER_URL}`);
  win.loadURL(PLAYER_URL);

  // Inject recording toolbar once the page is ready
  if (RECORD_MODE) {
    win.webContents.on('did-finish-load', () => {
      win.webContents.executeJavaScript(TOOLBAR_JS).catch(console.error);
    });
    // Re-inject if the page navigates internally (SPA route changes)
    win.webContents.on('did-navigate-in-page', () => {
      win.webContents.executeJavaScript(TOOLBAR_JS).catch(console.error);
    });
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------
ipcMain.handle('get-desktop-sources', async () => {
  const sources = await desktopCapturer.getSources({ types: ['window', 'screen'] });
  return sources.map(({ id, name }) => ({ id, name }));
});

ipcMain.handle('save-recording', (_, buffer, filename) => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const dest = path.join(OUTPUT_DIR, filename);
  fs.writeFileSync(dest, Buffer.from(buffer));
  console.log(`Recording saved: ${dest}`);
  return dest;
});

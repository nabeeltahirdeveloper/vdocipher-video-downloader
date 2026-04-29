const { app, BrowserWindow, ipcMain, desktopCapturer } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');

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

// ---------------------------------------------------------------------------
// Widevine CDM – load from the system Chrome installation on macOS
// ---------------------------------------------------------------------------
function findChromeWidevineCDM() {
  if (process.platform !== 'darwin') return null;

  const arch = process.arch === 'arm64' ? 'mac_arm64' : 'mac_x64';
  const browsers = [
    'Google Chrome',
    'Google Chrome Beta',
    'Chromium',
    'Brave Browser',
    'Microsoft Edge',
  ];

  for (const browser of browsers) {
    const base = `/Applications/${browser}.app/Contents/Frameworks`;
    // Chrome-style framework path
    const frameworkGlob = fs.readdirSync(base).find(d => d.endsWith('.framework')) || '';
    const cdmPath = path.join(
      base,
      frameworkGlob,
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
  } catch (_) {
    return '4.10.2710.0';
  }
}

const cdmPath = findChromeWidevineCDM();
if (cdmPath) {
  const version = readCDMVersion(cdmPath);
  console.log(`Widevine CDM found: ${cdmPath} (v${version})`);
  app.commandLine.appendSwitch('widevine-cdm-path', cdmPath);
  app.commandLine.appendSwitch('widevine-cdm-version', version);
} else {
  console.warn(
    'Widevine CDM not found. DRM-protected content may not play.\n' +
    'Install Google Chrome to enable DRM support.'
  );
}

// ---------------------------------------------------------------------------
// VDO Cipher config API
// ---------------------------------------------------------------------------
function fetchVideoConfig(otp, playbackInfo) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ otp, playbackInfo });
    const req = https.request(
      {
        hostname: 'dev.vdocipher.com',
        path: '/api/videos/config',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
          'Origin': 'https://player.vdocipher.com',
          'Referer': 'https://player.vdocipher.com/',
        },
      },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { reject(new Error(`Bad API response: ${e.message}\n${data}`)); }
        });
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// App bootstrap
// ---------------------------------------------------------------------------
let videoConfig = null;

app.whenReady().then(async () => {
  try {
    console.log('Fetching video configuration...');
    const api = await fetchVideoConfig(OTP, PLAYBACK_INFO);
    const sources = api.sources || [];

    // Prefer DASH (DRM-capable), fall back to HLS / plain MP4
    const pick = (pred) => sources.find(pred);
    const source =
      pick((s) => (s.type || '').includes('dash') || (s.src || '').endsWith('.mpd')) ||
      pick((s) => (s.type || '').includes('mpegURL') || (s.src || '').includes('.m3u8')) ||
      sources[0];

    if (!source) throw new Error('No video sources returned by VDO Cipher API');

    // License URL – may be nested under source.drm.widevine.url
    const licenseUrl =
      source?.drm?.widevine?.url ||
      source?.drm?.Widevine?.url ||
      'https://license.vdocipher.com/auth';

    const srcType = source.type ||
      (source.src?.endsWith('.mpd') ? 'application/dash+xml' : 'application/x-mpegURL');

    videoConfig = {
      src: source.src,
      type: srcType,
      licenseUrl,
      otp: OTP,
      recordMode: RECORD_MODE,
      outputDir: path.resolve(OUTPUT_DIR),
    };

    console.log(`Source: ${videoConfig.src}`);
  } catch (err) {
    console.error('Failed to fetch video config:', err.message);
    videoConfig = { error: err.message };
  }

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

  win.loadFile(path.join(__dirname, 'index.html'));
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------
ipcMain.handle('get-video-config', () => videoConfig);

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

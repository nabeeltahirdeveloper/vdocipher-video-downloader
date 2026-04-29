#!/usr/bin/env python3
"""
VDO Cipher Video Downloader
Downloads videos from VDO Cipher player URLs, with optional DRM bypass.
"""

import requests
import json
import base64
import urllib.parse
import os
import sys
import re
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse
import argparse


WIDEVINE_SCHEME = 'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed'
VDOCIPHER_CONFIG_API = "https://dev.vdocipher.com/api/videos/config"
VDOCIPHER_LICENSE_URL = "https://license.vdocipher.com/auth"


class DRMHandler:
    """Handles Widevine DRM key extraction for VDO Cipher content."""

    def __init__(self, session, device_path):
        self.session = session
        self.device_path = device_path

    def extract_pssh_from_mpd(self, mpd_url):
        """Parse MPD manifest and return the base64 Widevine PSSH box."""
        response = self.session.get(mpd_url)
        response.raise_for_status()

        # Strip default namespace so ElementTree can match tags simply
        xml_text = re.sub(r'\sxmlns="[^"]+"', '', response.text, count=1)
        root = ET.fromstring(xml_text)

        for cp in root.iter('ContentProtection'):
            scheme = cp.get('schemeIdUri', '').lower()
            if scheme == WIDEVINE_SCHEME:
                for child in cp:
                    if child.tag.endswith('}pssh') or child.tag == 'pssh':
                        pssh_b64 = (child.text or '').strip()
                        if pssh_b64:
                            return pssh_b64

        raise ValueError("No Widevine PSSH found in MPD manifest")

    def get_keys(self, pssh_b64, license_url, otp):
        """Send Widevine license challenge and return [(kid_hex, key_hex), ...]."""
        try:
            from pywidevine.cdm import Cdm
            from pywidevine.device import Device
            from pywidevine.pssh import PSSH
        except ImportError:
            raise ImportError(
                "pywidevine is required for DRM bypass. "
                "Install it with: pip install pywidevine"
            )

        device = Device.load(self.device_path)
        cdm = Cdm.from_device(device)
        session_id = cdm.open()

        try:
            pssh = PSSH(pssh_b64)
            challenge = cdm.get_license_challenge(session_id, pssh)

            headers = {
                'Content-Type': 'application/octet-stream',
                'X-VDO-Otp': otp,
            }
            resp = self.session.post(license_url, data=bytes(challenge), headers=headers)
            resp.raise_for_status()

            cdm.parse_license(session_id, resp.content)
            keys = [
                (key.kid.hex, key.key.hex())
                for key in cdm.get_keys(session_id)
                if key.type == 'CONTENT'
            ]
            return keys
        finally:
            cdm.close(session_id)


class VDOCipherDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://player.vdocipher.com/',
            'Origin': 'https://player.vdocipher.com'
        })

    def parse_url(self, url):
        """Parse VDO Cipher URL to extract OTP and playback info."""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            otp = params.get('otp', [None])[0]
            playback_info = params.get('playbackInfo', [None])[0]

            if not otp or not playback_info:
                raise ValueError("Missing OTP or playbackInfo in URL")

            decoded_info = base64.b64decode(playback_info).decode('utf-8')
            playback_data = json.loads(decoded_info)

            return {
                'otp': otp,
                'playback_info': playback_info,
                'video_id': playback_data.get('videoId')
            }
        except Exception as e:
            raise ValueError(f"Failed to parse URL: {str(e)}")

    def get_video_info(self, otp, playback_info):
        """Get video information and streaming URLs from VDO Cipher API."""
        # Method 1: Direct API call
        try:
            payload = {'otp': otp, 'playbackInfo': playback_info}
            response = self.session.post(VDOCIPHER_CONFIG_API, json=payload)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass

        # Method 2: Scrape player page
        try:
            player_url = f"https://player.vdocipher.com/v2/?otp={otp}&playbackInfo={playback_info}"
            response = self.session.get(player_url)

            if response.status_code == 200:
                content = response.text

                json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', content)
                if json_match:
                    try:
                        return json.loads(json_match.group(1))
                    except Exception:
                        pass

                sources = []
                for url in re.findall(r'https?://[^"\s]+\.m3u8[^"\s]*', content):
                    sources.append({'src': url, 'type': 'application/x-mpegURL', 'height': 720})
                for url in re.findall(r'https?://[^"\s]+\.mp4[^"\s]*', content):
                    sources.append({'src': url, 'type': 'video/mp4', 'height': 720})
                for url in re.findall(r'https?://[^"\s]+\.mpd[^"\s]*', content):
                    sources.append({'src': url, 'type': 'application/dash+xml', 'height': 720})

                if sources:
                    return {'sources': sources}
        except Exception as e:
            print(f"Player scraping failed: {e}")

        raise Exception("Could not extract video information from any method")

    # ------------------------------------------------------------------
    # DRM-aware download helpers
    # ------------------------------------------------------------------

    def _get_license_url(self, source):
        """Extract license server URL from a source dict returned by the API."""
        # API may nest it under source['drm']['widevine']['url'] or similar
        drm = source.get('drm') or {}
        widevine = drm.get('widevine') or drm.get('Widevine') or {}
        return widevine.get('url') or widevine.get('licenseUrl') or VDOCIPHER_LICENSE_URL

    def _is_drm_source(self, source):
        return bool(source.get('drm')) or '.mpd' in source.get('src', '')

    def download_with_drm_skip(self, source, otp, filename, device_path):
        """Download a DRM-protected DASH stream, decrypt, and save to filename."""
        mpd_url = source['src']
        license_url = self._get_license_url(source)

        print(f"DRM-protected stream detected: {mpd_url}")
        print(f"License server: {license_url}")

        # Step 1 – extract Widevine keys
        print("Extracting Widevine keys...")
        handler = DRMHandler(self.session, device_path)
        pssh_b64 = handler.extract_pssh_from_mpd(mpd_url)
        keys = handler.get_keys(pssh_b64, license_url, otp)

        if not keys:
            raise Exception("No content keys returned by license server")

        key_str = ', '.join(f"{kid}:{key}" for kid, key in keys)
        print(f"Keys obtained: {key_str}")

        # Step 2 – download encrypted segments with ffmpeg
        encrypted_path = filename + '.enc.mp4'
        print(f"Downloading encrypted stream to: {encrypted_path}")
        dl_cmd = [
            'ffmpeg', '-y',
            '-i', mpd_url,
            '-c', 'copy',
            encrypted_path
        ]
        result = subprocess.run(dl_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg download failed:\n{result.stderr}")

        # Step 3 – decrypt with mp4decrypt (Bento4)
        print(f"Decrypting to: {filename}")
        key_args = []
        for kid, key in keys:
            key_args += ['--key', f'{kid}:{key}']
        dec_cmd = ['mp4decrypt'] + key_args + [encrypted_path, filename]

        result = subprocess.run(dec_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"mp4decrypt failed:\n{result.stderr}")

        os.remove(encrypted_path)
        print(f"Download and decryption complete: {filename}")
        return True

    # ------------------------------------------------------------------
    # Plain (non-DRM) download helpers
    # ------------------------------------------------------------------

    def download_m3u8_playlist(self, m3u8_url, filename):
        """Download M3U8 playlist and mux to MP4 via ffmpeg."""
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("ffmpeg is required for M3U8 downloads. Please install ffmpeg.")
            return False

        print(f"Downloading M3U8 stream: {filename}")
        cmd = [
            'ffmpeg', '-y',
            '-i', m3u8_url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            filename
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in process.stderr:
            if 'time=' in line:
                print(f"\r{line.strip()}", end="", flush=True)
        process.wait()

        if process.returncode == 0:
            print(f"\nDownload completed: {filename}")
            return True
        print(f"\nffmpeg failed with return code {process.returncode}")
        return False

    def download_video(self, video_url, filename, chunk_size=8192):
        """Download video from a direct streaming URL."""
        if '.m3u8' in video_url:
            return self.download_m3u8_playlist(video_url, filename)

        print(f"Downloading: {filename}")
        response = self.session.get(video_url, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0

        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        print(f"\rProgress: {downloaded / total_size * 100:.1f}%", end="", flush=True)

        print(f"\nDownload completed: {filename}")
        return True

    # ------------------------------------------------------------------
    # High-level processing
    # ------------------------------------------------------------------

    def process_url(self, url, output_dir=".", skip_drm=False, device_path=None):
        """Process a VDO Cipher URL and download the video."""
        try:
            print(f"Processing URL: {url}")
            url_data = self.parse_url(url)
            print(f"Video ID: {url_data['video_id']}")

            print("Getting video information...")
            video_info = self.get_video_info(url_data['otp'], url_data['playback_info'])

            sources = video_info.get('sources', [])
            if not sources:
                raise Exception("No video sources found")

            # Separate DRM-protected DASH sources from plain sources
            drm_sources = [s for s in sources if self._is_drm_source(s)]
            plain_sources = [s for s in sources if not self._is_drm_source(s)]

            if drm_sources and skip_drm:
                if not device_path:
                    raise ValueError(
                        "DRM content detected. Provide a Widevine device file with --device <path.wvd>"
                    )
                source = max(drm_sources, key=lambda x: int(x.get('height', 0)))
                quality = f"{source.get('height', 'unknown')}p"
                filename = os.path.join(output_dir, f"{url_data['video_id']}_{quality}.mp4")
                print(f"Found DRM-protected stream: {quality}")
                return self.download_with_drm_skip(source, url_data['otp'], filename, device_path)

            if plain_sources:
                source = max(plain_sources, key=lambda x: int(x.get('height', 0)))
            elif sources:
                source = max(sources, key=lambda x: int(x.get('height', 0)))
                if self._is_drm_source(source) and not skip_drm:
                    print(
                        "Warning: This video appears to be DRM-protected. "
                        "Re-run with --skip-drm --device <path.wvd> to bypass DRM."
                    )
            else:
                raise Exception("No usable video sources found")

            quality = f"{source.get('height', 'unknown')}p"
            filename = os.path.join(output_dir, f"{url_data['video_id']}_{quality}.mp4")
            print(f"Found video: {quality} quality")

            return self.download_video(source['src'], filename)

        except Exception as e:
            print(f"Error processing {url}: {str(e)}")
            return False

    # ------------------------------------------------------------------
    # Config-based direct download (from browser network tab JSON)
    # ------------------------------------------------------------------

    def download_from_config(self, config, otp, output_dir='.', device_path=None):
        """
        Download using the VDO Cipher player config JSON extracted from the
        browser network tab, combined with the OTP from the player URL.

        The config JSON contains the DASH manifest URL and Widevine license
        server template.  The OTP is substituted for the ':authToken' placeholder
        in the license URL.
        """
        if isinstance(config, str):
            config = json.loads(config)

        dash = config.get('dash', {})
        mpd_url = dash.get('manifest')
        if not mpd_url:
            raise ValueError("Config does not contain a 'dash.manifest' URL")

        # Build the actual license URL – substitute :authToken with the OTP
        license_servers = dash.get('licenseServers', {})
        wv_url_template = (
            license_servers.get('com.widevine.alpha') or
            'https://license.vdocipher.com/auth3/wv/:authToken'
        )
        license_url = wv_url_template.replace(':authToken', otp)

        # Derive a stable filename from the content ID embedded in the manifest URL
        content_id_match = re.search(r'/manifest/([a-f0-9]+)\.mpd', mpd_url)
        video_id = content_id_match.group(1) if content_id_match else 'video'
        title = re.sub(r'[^\w\-]', '_', config.get('title', video_id))

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f'{title}.mp4')

        print(f"Video   : {config.get('title', video_id)}")
        print(f"Manifest: {mpd_url}")
        print(f"License : {license_url}")

        if not device_path:
            raise ValueError(
                "Widevine device file required for DRM content.\n"
                "Provide one with --device <path.wvd>\n\n"
                "If you do not have a .wvd file, the content cannot be decrypted.\n"
                "Screen recording of DRM-protected Chrome windows is blocked at the\n"
                "macOS WindowServer level (psr:true in VDO Cipher config) and cannot\n"
                "be bypassed with software screen capture tools."
            )

        handler = DRMHandler(self.session, device_path)

        print("Extracting Widevine PSSH from manifest…")
        pssh_b64 = handler.extract_pssh_from_mpd(mpd_url)

        print("Requesting Widevine license…")
        keys = handler.get_keys(pssh_b64, license_url, otp)
        if not keys:
            raise RuntimeError("License server returned no content keys")

        print(f"Keys: {', '.join(f'{k}:{v}' for k, v in keys)}")

        encrypted_path = output_path + '.enc.mp4'
        print(f"Downloading encrypted DASH stream…")
        dl_cmd = ['ffmpeg', '-y', '-i', mpd_url, '-c', 'copy', encrypted_path]
        result = subprocess.run(dl_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg download failed:\n{result.stderr}")

        print(f"Decrypting…")
        key_args = []
        for kid, key in keys:
            key_args += ['--key', f'{kid}:{key}']
        dec_cmd = ['mp4decrypt'] + key_args + [encrypted_path, output_path]
        result = subprocess.run(dec_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"mp4decrypt failed:\n{result.stderr}")

        os.remove(encrypted_path)
        print(f"Saved: {output_path}")
        return True

    # ------------------------------------------------------------------
    # Player / screen-record mode
    # ------------------------------------------------------------------

    _CHROME_PATHS = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    ]

    def _find_chrome(self):
        return next((p for p in self._CHROME_PATHS if os.path.exists(p)), None)

    def _list_avfoundation_devices(self):
        """Return (screen_index, device_list_text) from avfoundation."""
        result = subprocess.run(
            ['ffmpeg', '-f', 'avfoundation', '-list_devices', 'true', '-i', ''],
            capture_output=True, text=True
        )
        output = result.stderr
        # Lines look like: [AVFoundation ...] [1] Capture screen 0
        screens = re.findall(r'\[(\d+)\] Capture screen', output)
        return screens[0] if screens else '0', output

    def _check_screen_permission(self, screen_idx, output_file):
        """
        Do a 1-second test capture and check if the file contains real video data.
        Returns True if the permission is granted, False if blank/black.
        """
        test_file = output_file + '.permission_test.mp4'
        test_cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-f', 'avfoundation', '-framerate', '5', '-t', '1',
            '-i', screen_idx,
            '-vf', 'blackdetect=d=0.5:pix_th=0.1',
            '-vcodec', 'libx264', '-preset', 'ultrafast',
            test_file
        ]
        result = subprocess.run(test_cmd, capture_output=True, text=True)
        try:
            size = os.path.getsize(test_file)
            os.remove(test_file)
            # A valid 1-second capture at 5fps should be well over 5 KB;
            # macOS returns a tiny/empty file when permission is denied.
            return size > 5000
        except FileNotFoundError:
            return False

    def play_in_player(self, url, output_dir='.', record=False):
        """Open VDO Cipher URL in Chrome (native Widevine) and optionally record via ffmpeg."""
        import time
        from datetime import datetime

        url_data = self.parse_url(url)
        chrome   = self._find_chrome()

        ffmpeg_proc = None
        output_file = None

        # ------------------------------------------------------------------
        # Screen recording setup
        # ------------------------------------------------------------------
        if record:
            os.makedirs(output_dir, exist_ok=True)
            ts          = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(os.path.abspath(output_dir), f'recording_{ts}.mp4')

            screen_idx, device_list = self._list_avfoundation_devices()
            print(f"Detected screen device index: {screen_idx}")

            # Permission check
            print("Checking screen recording permission…")
            if not self._check_screen_permission(screen_idx, output_file):
                print()
                print("=" * 60)
                print("  BLANK SCREEN – Screen Recording permission required")
                print("=" * 60)
                print()
                print("  1. Open:  System Settings → Privacy & Security")
                print("            → Screen Recording")
                print("  2. Enable the toggle next to your Terminal app")
                print("     (Terminal, iTerm2, Warp, etc.)")
                print("  3. Restart your terminal completely")
                print("  4. Re-run this command")
                print()
                print("  If the toggle is already on, try toggling it off")
                print("  and on again, then restart the terminal.")
                print()
                return

            # 5-second countdown so the user can switch to the video
            print(f"\nRecording to: {output_file}")
            print("Chrome will open now. Switch to it and start the video.")
            for i in range(5, 0, -1):
                print(f"  Recording starts in {i}…", end='\r')
                time.sleep(1)
            print("  Recording started!          ")

            ffmpeg_cmd = [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-f', 'avfoundation',
                '-framerate', '30',
                '-capture_cursor', '1',
                '-i', screen_idx,
                '-vcodec', 'libx264',
                '-preset', 'ultrafast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                output_file,
            ]
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # ------------------------------------------------------------------
        # Open video in Chrome
        # ------------------------------------------------------------------
        print(f"Opening video {url_data['video_id']} in Chrome…")
        if chrome:
            subprocess.Popen([chrome, f'--app={url}'])
        else:
            subprocess.run(['open', url])

        # ------------------------------------------------------------------
        # Wait, then stop ffmpeg
        # ------------------------------------------------------------------
        if ffmpeg_proc:
            print("Recording in progress. Press Enter (or Ctrl-C) to stop and save.")
            try:
                input()
            except KeyboardInterrupt:
                pass

            try:
                ffmpeg_proc.stdin.write(b'q\n')
                ffmpeg_proc.stdin.flush()
                ffmpeg_proc.wait(timeout=8)
            except Exception:
                ffmpeg_proc.terminate()
                ffmpeg_proc.wait()

            print(f"Recording saved: {output_file}")
        else:
            print("Chrome opened. Close the browser window when done.")

    def process_file(self, file_path, output_dir="./downloaded-videos", skip_drm=False, device_path=None):
        """Process URLs from a text file."""
        try:
            os.makedirs(output_dir, exist_ok=True)
            print(f"Output directory: {output_dir}")

            with open(file_path, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip()]

            if not urls:
                print("No URLs found in the file.")
                return

            print(f"Found {len(urls)} URLs to process")
            successful = 0
            failed = 0

            for i, url in enumerate(urls, 1):
                if not url.startswith('https://player.vdocipher.com/'):
                    print(f"[{i}/{len(urls)}] Skipping invalid URL: {url}")
                    failed += 1
                    continue

                print(f"\n[{i}/{len(urls)}] Processing URL {i}...")
                if self.process_url(url, output_dir, skip_drm=skip_drm, device_path=device_path):
                    successful += 1
                else:
                    failed += 1
                print("-" * 50)

            print(f"\nProcessing complete!")
            print(f"Successful downloads: {successful}")
            print(f"Failed downloads: {failed}")

        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found.")
        except Exception as e:
            print(f"Error processing file: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description='VDO Cipher Video Downloader',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Open in Chrome (DRM plays natively)
  %(prog)s --url "https://player.vdocipher.com/v2/?otp=...&playbackInfo=..." --player

  # Record screen while playing (requires ffmpeg)
  %(prog)s --url "..." --player --screen-record --output ./downloads

  # Download + decrypt from browser config JSON (requires mp4decrypt + .wvd)
  %(prog)s --config config.json --otp "20160313vers..." --device device.wvd
"""
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--url', help='Single VDO Cipher player URL')
    input_group.add_argument('--file', '-f', help='Text file with one VDO Cipher URL per line')
    input_group.add_argument('--config', metavar='PATH',
                             help='VDO Cipher player config JSON file (from browser network tab)')

    parser.add_argument('--otp', metavar='TOKEN',
                        help='OTP token from the player URL (required with --config)')
    parser.add_argument('-o', '--output', default='./downloaded-videos',
                        help='Output directory (default: ./downloaded-videos)')

    # DRM bypass
    parser.add_argument('--skip-drm', action='store_true',
                        help='Bypass Widevine DRM via pywidevine (requires --device and mp4decrypt)')
    parser.add_argument('--device', metavar='PATH',
                        help='Widevine device file (.wvd) for key extraction')

    # Player / screen-record
    parser.add_argument('--player', action='store_true',
                        help='Open the video in Chrome (native DRM support)')
    parser.add_argument('--screen-record', action='store_true',
                        help='Record the screen while the player is open (use with --player)')

    args = parser.parse_args()

    if args.skip_drm and not args.device:
        parser.error("--skip-drm requires --device <path-to-.wvd-file>")
    if args.screen_record and not args.player:
        parser.error("--screen-record requires --player")
    if args.config and not args.otp:
        parser.error("--config requires --otp <token>")

    downloader = VDOCipherDownloader()

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as fh:
            config = json.load(fh)
        os.makedirs(args.output, exist_ok=True)
        try:
            downloader.download_from_config(
                config, args.otp, args.output, device_path=args.device
            )
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.url:
        if not args.url.startswith('https://player.vdocipher.com/'):
            print("Error: Please provide a valid VDO Cipher URL")
            sys.exit(1)
        os.makedirs(args.output, exist_ok=True)
        if args.player:
            downloader.play_in_player(args.url, args.output, record=args.screen_record)
        else:
            downloader.process_url(args.url, args.output,
                                   skip_drm=args.skip_drm, device_path=args.device)

    elif args.file:
        if args.player:
            with open(args.file, 'r', encoding='utf-8') as fh:
                urls = [l.strip() for l in fh if l.strip()]
            for url in urls:
                if url.startswith('https://player.vdocipher.com/'):
                    downloader.play_in_player(url, args.output, record=args.screen_record)
        else:
            downloader.process_file(args.file, args.output,
                                    skip_drm=args.skip_drm, device_path=args.device)


if __name__ == "__main__":
    main()

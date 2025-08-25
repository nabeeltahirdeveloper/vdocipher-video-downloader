#!/usr/bin/env python3
"""
VDO Cipher Video Downloader
Downloads videos from VDO Cipher player URLs
"""

import requests
import json
import base64
import urllib.parse
import os
import sys
from urllib.parse import parse_qs, urlparse
import argparse


class VDOCipherDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://player.vdocipher.com/',
            'Origin': 'https://player.vdocipher.com'
        })

    def parse_url(self, url):
        """Parse VDO Cipher URL to extract OTP and playback info"""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            
            otp = params.get('otp', [None])[0]
            playback_info = params.get('playbackInfo', [None])[0]
            
            if not otp or not playback_info:
                raise ValueError("Missing OTP or playbackInfo in URL")
            
            # Decode playback info
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
        """Get video information and streaming URLs"""
        try:
            # Try multiple possible API endpoints
            endpoints = [
                "https://dev.vdocipher.com/api/videos/config",
                "https://player.vdocipher.com/playerAssets/1.6.6/js/dist/main.bundle.js",
                f"https://player.vdocipher.com/v2/?otp={otp}&playbackInfo={playback_info}"
            ]
            
            # Method 1: Direct API call
            try:
                payload = {
                    'otp': otp,
                    'playbackInfo': playback_info
                }
                
                response = self.session.post(endpoints[0], json=payload)
                if response.status_code == 200:
                    return response.json()
            except:
                pass
            
            # Method 2: Scrape from player page
            try:
                player_url = f"https://player.vdocipher.com/v2/?otp={otp}&playbackInfo={playback_info}"
                response = self.session.get(player_url)
                
                if response.status_code == 200:
                    # Look for video config in the HTML
                    content = response.text
                    
                    # Search for common patterns where video URLs might be stored
                    import re
                    
                    # Look for m3u8 URLs
                    m3u8_pattern = r'https?://[^"\s]+\.m3u8[^"\s]*'
                    m3u8_urls = re.findall(m3u8_pattern, content)
                    
                    # Look for mp4 URLs
                    mp4_pattern = r'https?://[^"\s]+\.mp4[^"\s]*'
                    mp4_urls = re.findall(mp4_pattern, content)
                    
                    # Look for JSON config
                    json_pattern = r'window\.__INITIAL_STATE__\s*=\s*({.*?});'
                    json_match = re.search(json_pattern, content)
                    
                    if json_match:
                        try:
                            config = json.loads(json_match.group(1))
                            return config
                        except:
                            pass
                    
                    # Return URLs found
                    sources = []
                    for url in m3u8_urls + mp4_urls:
                        sources.append({
                            'src': url,
                            'type': 'application/x-mpegURL' if '.m3u8' in url else 'video/mp4',
                            'height': 720  # Default quality
                        })
                    
                    if sources:
                        return {'sources': sources}
                        
            except Exception as e:
                print(f"Player scraping failed: {e}")
            
            raise Exception("Could not extract video information from any method")
            
        except Exception as e:
            raise Exception(f"Failed to get video info: {str(e)}")

    def download_m3u8_playlist(self, m3u8_url, filename):
        """Download M3U8 playlist and convert to MP4"""
        try:
            import subprocess
            
            # Check if ffmpeg is available
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("ffmpeg is required for M3U8 downloads. Please install ffmpeg.")
                return False
            
            print(f"Downloading M3U8 stream: {filename}")
            
            # Use ffmpeg to download and convert M3U8 to MP4
            cmd = [
                'ffmpeg',
                '-i', m3u8_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-y',  # Overwrite output file
                filename
            ]
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Monitor progress
            for line in process.stderr:
                if 'time=' in line:
                    print(f"\rDownloading... {line.strip()}", end="", flush=True)
            
            process.wait()
            
            if process.returncode == 0:
                print(f"\nDownload completed: {filename}")
                return True
            else:
                print(f"\nffmpeg failed with return code {process.returncode}")
                return False
                
        except Exception as e:
            print(f"M3U8 download failed: {str(e)}")
            return False

    def download_video(self, video_url, filename, chunk_size=8192):
        """Download video from streaming URL"""
        try:
            # Check if it's an M3U8 playlist
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
                            progress = (downloaded / total_size) * 100
                            print(f"\rProgress: {progress:.1f}%", end="", flush=True)
            
            print(f"\nDownload completed: {filename}")
            return True
            
        except Exception as e:
            print(f"Download failed: {str(e)}")
            return False

    def process_url(self, url, output_dir="."):
        """Process VDO Cipher URL and download video"""
        try:
            print(f"Processing URL: {url}")
            url_data = self.parse_url(url)
            print(f"Video ID: {url_data['video_id']}")
            
            print("Getting video information...")
            video_info = self.get_video_info(url_data['otp'], url_data['playback_info'])
            
            # Extract video sources
            sources = video_info.get('sources', [])
            if not sources:
                raise Exception("No video sources found")
            
            # Get the highest quality source
            best_source = max(sources, key=lambda x: int(x.get('height', 0)))
            video_url = best_source['src']
            quality = f"{best_source.get('height', 'unknown')}p"
            
            print(f"Found video: {quality} quality")
            
            # Generate filename
            filename = f"{url_data['video_id']}_{quality}.mp4"
            filepath = os.path.join(output_dir, filename)
            
            # Download video
            success = self.download_video(video_url, filepath)
            
            if success:
                print(f"Video saved to: {filepath}")
                return True
            else:
                print("Download failed")
                return False
                
        except Exception as e:
            print(f"Error processing {url}: {str(e)}")
            return False

    def process_file(self, file_path, output_dir="./downloaded-videos"):
        """Process URLs from a text file"""
        try:
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            print(f"Output directory: {output_dir}")
            
            # Read URLs from file
            with open(file_path, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip()]
            
            if not urls:
                print("No URLs found in the file.")
                return
            
            print(f"Found {len(urls)} URLs to process")
            
            successful_downloads = 0
            failed_downloads = 0
            
            for i, url in enumerate(urls, 1):
                if not url.startswith('https://player.vdocipher.com/'):
                    print(f"[{i}/{len(urls)}] Skipping invalid URL: {url}")
                    failed_downloads += 1
                    continue
                
                print(f"\n[{i}/{len(urls)}] Processing URL {i}...")
                success = self.process_url(url, output_dir)
                
                if success:
                    successful_downloads += 1
                else:
                    failed_downloads += 1
                
                print("-" * 50)
            
            print(f"\nProcessing complete!")
            print(f"Successful downloads: {successful_downloads}")
            print(f"Failed downloads: {failed_downloads}")
            
        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found.")
        except Exception as e:
            print(f"Error processing file: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description='VDO Cipher Video Downloader')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--url', help='Single VDO Cipher video URL')
    group.add_argument('--file', '-f', help='Text file containing VDO Cipher URLs (one per line)')
    parser.add_argument('-o', '--output', default='./downloaded-videos', help='Output directory (default: ./downloaded-videos)')
    
    args = parser.parse_args()
    
    downloader = VDOCipherDownloader()
    
    if args.url:
        # Single URL mode
        if not args.url.startswith('https://player.vdocipher.com/'):
            print("Error: Please provide a valid VDO Cipher URL")
            sys.exit(1)
        
        # Create output directory if it doesn't exist
        os.makedirs(args.output, exist_ok=True)
        downloader.process_url(args.url, args.output)
    
    elif args.file:
        # File mode
        downloader.process_file(args.file, args.output)


if __name__ == "__main__":
    main()
# VDOCipher Video Downloader

A Python tool to download videos from VDOCipher player URLs. This tool can extract video streams from VDOCipher-protected content and download them locally.

## Features

- Download videos from VDOCipher player URLs
- Support for both single URL and batch processing
- Automatic quality detection (downloads highest available quality)
- Support for M3U8 playlist downloads (requires ffmpeg)
- Progress tracking during downloads
- Customizable output directory

## Requirements

- Python 3.6+
- requests library
- ffmpeg (optional, required for M3U8 playlist downloads)

## Installation

1. Clone or download this repository:
```bash
git clone https://github.com/nabeeltahirdeveloper/vdocipher-video-downloader.git
cd vdocipher-video-downloader
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. (Optional) Install ffmpeg for M3U8 support:
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)
   - **macOS**: `brew install ffmpeg`
   - **Ubuntu/Debian**: `sudo apt install ffmpeg`

## Usage

### Command Line Interface

#### Download a single video:
```bash
python vdocipher_downloader.py --url "https://player.vdocipher.com/v2/?otp=YOUR_OTP&playbackInfo=YOUR_PLAYBACK_INFO"
```

#### Download multiple videos from a file:
```bash
python vdocipher_downloader.py --file links.txt
```

#### Specify custom output directory:
```bash
python vdocipher_downloader.py --file links.txt --output /path/to/download/folder
```

### Arguments

- `--url`: Single VDOCipher video URL to download
- `--file`, `-f`: Text file containing VDOCipher URLs (one per line)
- `--output`, `-o`: Output directory for downloaded videos (default: `./downloaded-videos`)

### Input File Format

Create a text file (e.g., `links.txt`) with one VDOCipher URL per line:

```
https://player.vdocipher.com/v2/?otp=OTP1&playbackInfo=INFO1
https://player.vdocipher.com/v2/?otp=OTP2&playbackInfo=INFO2
https://player.vdocipher.com/v2/?otp=OTP3&playbackInfo=INFO3
```

## Project Structure

```
vdocipher-video-downloader/
├── vdocipher_downloader.py    # Main downloader script
├── requirements.txt           # Python dependencies
├── links.txt                  # Example URLs file
├── downloaded-videos/         # Default output directory
└── README.md                  # This file
```

## How It Works

1. **URL Parsing**: Extracts OTP and playbackInfo parameters from VDOCipher URLs
2. **Video Information Extraction**: Attempts to fetch video metadata using multiple methods:
   - Direct API calls
   - Player page scraping
   - Pattern matching for video URLs
3. **Quality Selection**: Automatically selects the highest available quality
4. **Download**: Downloads the video file with progress tracking

## Output

Downloaded videos are saved with the following naming convention:
```
{video_id}_{quality}.mp4
```

Example: `7709825a29b74512805187083cc53887_720p.mp4`

## Error Handling

The tool includes comprehensive error handling for:
- Invalid URLs
- Network connectivity issues
- Missing video sources
- Download interruptions
- ffmpeg availability (for M3U8 files)

## Limitations

- Only works with VDOCipher player URLs
- Some protected content may not be accessible
- M3U8 downloads require ffmpeg installation
- Download success depends on the specific VDOCipher implementation

## Legal Notice

This tool is for educational purposes only. Users are responsible for ensuring they have the right to download and use the content. Respect copyright laws and terms of service of the content providers.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/improvement`)
3. Commit your changes (`git commit -am 'Add new feature'`)
4. Push to the branch (`git push origin feature/improvement`)
5. Create a Pull Request

## License

This project is provided as-is for educational purposes. Use at your own discretion and ensure compliance with applicable laws and terms of service.

## Troubleshooting

### Common Issues

1. **"ffmpeg is required" error**:
   - Install ffmpeg using your package manager or download from [ffmpeg.org](https://ffmpeg.org/)

2. **"No video sources found" error**:
   - The URL may be invalid or the video may be heavily protected
   - Try accessing the video in a browser first to ensure it works

3. **Download fails**:
   - Check your internet connection
   - Verify the URL is correct and accessible
   - Some videos may have additional protection measures

### Getting Help

If you encounter issues:
1. Check that your VDOCipher URL is valid
2. Ensure all dependencies are installed
3. Try running with a single URL first before batch processing
4. Check the console output for specific error messages
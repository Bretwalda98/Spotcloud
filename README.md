# Spotcloud
Spotify and Soundcloud playlist audio downloader

## Building a Windows Executable

1. Install the required dependencies:
   ```bash
   pip install spotdl yt-dlp pyinstaller
   ```
2. Run PyInstaller to create a standalone EXE:
   ```bash
   pyinstaller --onefile --windowed -n Spotcloud main.py
   ```
   The resulting `Spotcloud.exe` will be located in the `dist` folder.

To run the unit tests instead of launching the GUI, execute:

```bash
python main.py test
```

# MP4 export: minimize inherited source metadata

Both existing renderers use FFmpeg to make H.264/AAC MP4 output:

- Telegram video-circle composition: `app/telegram_studio.py`
- Ordinary vertical composition: `app/instagram_studio.py`

All **new renders** now use these **output** arguments:

```
-map_metadata -1 -map_metadata:s -1 -map_chapters -1
```

They prevent the output from inheriting optional global/stream metadata
(such as title, comment or source application labels) and chapters. Explicitly
mapped video and audio, composition, frame rate, quality settings and faststart
stay as before. Existing finished videos are not modified and sources remain
untouched. No project settings, accounts or videos are deleted.

This is **normal privacy-oriented export hygiene**, not a tool to hide stolen
content or claim someone else's authorship. FFmpeg can still add its standard
encoder/container tags, and visual/audio content matching may still identify
the same footage. Do not claim these exports prove original authorship.

## Verification (Windows PowerShell)

Stop the renderer before updating local code. From the project folder:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_render_clean_mp4_metadata.py"
```

Then render a **new video** and inspect its container metadata:

```powershell
ffprobe -v error -show_entries format_tags:stream_tags -of json "C:\PATH\TO\OUTPUT.mp4"
```

Common MP4 structural entries and FFmpeg `encoder` tags may remain;
the test verifies source title/comment-like metadata is not carried forward.

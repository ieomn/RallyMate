# Demo data

The reproducible smoke-test assets are:

- `pexels-tennis-serve-4902773.mp4`
- Source page: https://www.pexels.com/video/a-tennis-player-serving-the-ball-4902773/
- `pexels-tennis-match-992693.mp4`
- Source page: https://www.pexels.com/video/people-playing-tennis-992693/
- The source page marks the clip as free to use under the Pexels license.

The MP4 is intentionally ignored by Git. Recreate it with:

```powershell
.\.venv\Scripts\python.exe .\scripts\download_assets.py --root .
```

The serve clip validates close-player detection, ball/racket detection, pose
association, tracking continuity, and the rejection of an unusable court view.
The match clip validates multiple players and automatic/manual court-region
paths. These clips validate integration behavior; they are not a training or
accuracy benchmark dataset.

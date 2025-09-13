# ImgStats

A lightweight FastAPI-based dashboard for monitoring factory vision inspection results.  
It scans station image folders, aggregates OK/NG counts, computes yield, and displays the latest images (including recent NG thumbnails).  
Designed to run on NAS with Docker in offline environments.

---

## ✨ Features
- **Automatic directory scan** – parses filenames like `OK-20250906-102131-132.jpg`
- **Statistics per station & model** – total, OK, NG, yield
- **Time range selection** – Last 1h / 1d / 1w / Custom
- **Last Image / Last NG Images** preview with file path
- **Dark/Light mode** toggle
- **Runs offline on NAS** – no external dependencies, only local volumes

---

## 🚀 Quickstart (Laptop)

Generate some test images:
```bash
python gen_images.py
```

Run locally with Docker Compose:
```bash
docker compose -f docker-compose.yml up -d
# Open http://localhost:8080
```

---

## 📦 Deploy on NAS (offline)

1. Build and push/publish image on your dev machine:
```bash
docker build -t yourname/imgstats:1.0.0 .
docker save -o imgstats_1.0.0.tar imgstats:1.0.0
```

2. Transfer the tar to NAS and load:
```bash
docker load -i imgstats_1.0.0.tar
```

3. Start with Docker Compose:
```bash
docker compose -f docker-compose.yml up -d
# Open http://<NAS_IP>:8080
```

---

## ⚙️ Configuration

| Env var             | Default            | Description                                          |
|---------------------|--------------------|------------------------------------------------------|
| `WATCH_DIR`         | /data              | Directory to scan for images                         |
| `DB_PATH`           | /state/data.sqlite | SQLite database + thumbnails cache                   |
| `POLL_INTERVAL_SEC` | 60                 | Polling interval for new files                       |
| `RECENT_MTIME_MIN`  | 60                 | Time window for initial scan (e.g. 1440 for 1 day)   |
| `MIN_FILE_AGE_SEC`  | 2                  | Ignore files younger than this (avoid partial write) |
| `NG_PREVIEW_COUNT`  | 3                  | How many recent NG images to preview                 |
| `FILENAME_REGEX`    | `^(OK/NG)-YYYYMMDD-HHMMSS-COUNT.(jpg|jpeg|png)` | Regex pattern for filenames |

---

## 🗂️ Volumes

- `/data` → read-only image root directory (stations/models inside)  
- `/state` → SQLite database and generated thumbnails  

Example directory layout:
```
IMAGES/
└── S9/
    └── OR-3CT/
        ├── OK-20250906-102131-132.jpg
        ├── NG-20250906-102431-133.jpg
        └── ...
```

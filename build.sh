#!/bin/bash
# build.sh — 로켓 대시보드 재빌드 (fetch → analyze → dashboard → Pages 파일 갱신)
# 원본 파이프라인: Desktop/AI/research/space/events/launches (SSOT)
# 이 저장소는 GitHub Pages 배포 번들 — 스크립트 수정은 원본에서 하고 재푸시.
set -euo pipefail
cd "$(dirname "$0")"

python3 scripts/fetch.py
python3 scripts/analyze.py
python3 scripts/dashboard.py

cp dashboard/rocket_dashboard.html index.html
cp dashboard/rocket_dashboard_data.json rocket_dashboard_data.json

# data/raw 14일 초과분 정리 (저장소 비대 방지)
python3 - <<'EOF'
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y%m%d")
for f in Path("data/raw").iterdir():
    m = re.match(r"^(\d{8})_", f.name)
    if m and m.group(1) < cutoff:
        f.unlink()
        print(f"pruned {f.name}")
EOF

echo "✅ build done — index.html / rocket_dashboard_data.json updated"

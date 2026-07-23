# Rocket Launch Intelligence Dashboard

로켓 발사 이벤트 트래킹 대시보드 (SpaceX · Rocket Lab · Blue Origin · Firefly).

- **Live**: https://ujin-kkk.github.io/rocket-dashboard/
- 데이터 소스: [Launch Library 2](https://ll.thespacedevs.com) (공개 API, 키 불필요)
- 자동 갱신: Claude Code 클라우드 루틴이 2일에 1번 `build.sh` 실행 후 push

## 구조

```
scripts/fetch.py      # LL2 API 수집 → data/raw/
scripts/analyze.py    # 지표 산출 → data/raw/*_analysis_TSR_rocket_metrics.json
scripts/dashboard.py  # 단일 HTML 대시보드 생성
build.sh              # 위 3단계 실행 + index.html/데이터 갱신 + 14일 초과 raw 정리
index.html            # GitHub Pages 서빙 본체 (rocket_dashboard_data.json 을 fetch)
vendor/               # Tabulator (self-hosted)
```

## 수동 갱신

```bash
bash build.sh
git add -A && git commit -m "update $(date -u +%F)" && git push
```

> 이 저장소는 배포 번들입니다. 파이프라인 원본(SSOT)은 로컬
> `Desktop/AI/research/space/events/launches/` — 스크립트 수정은 원본에서.

#!/usr/bin/env python3
# ════════════════════════════════════════
# 대시보드 실행 방법
# ════════════════════════════════════════
# 1. 데이터 갱신 (매일 자동 실행됨 / 수동 시):
#    bash scripts/run_rocket_research.sh
#
# 2. 로컬 서버 시작:
#    cd exports && python3 -m http.server 8080
#
# 3. 브라우저에서 열기:
#    http://localhost:8080/rocket_dashboard.html
#
# ※ 파일을 더블클릭(file://)으로 열면 동작하지 않습니다.
#   (브라우저 보안 정책상 file:// 에서는 fetch 가 차단되며,
#    이 대시보드는 fetch('rocket_dashboard_data.json') 으로
#    데이터를 비동기 로드합니다.)
# ════════════════════════════════════════
"""
generate_rocket_dashboard.py — Phase 3

data/raw/*_analysis_TSR_rocket_metrics.json + 원본 raw 들을 합쳐
exports/rocket_dashboard.html (단일 파일, 외부 CDN 허용) 을 만든다.

탭:
  1. 📅 발사 캘린더 (Plotly 간트, 향후 90일)
  2. 🔭 로켓 스펙 비교 (카드 + LEO 바)
  3. 📜 발사 이력 (Tabulator + 도넛)
  4. 📈 주식 인사이트 (Plotly bar/line/pie)
  5. 🏷️ 신뢰도 현황
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EXPORTS_DIR = ROOT / "dashboard"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")
NOW_ISO = datetime.now(timezone.utc).isoformat(timespec="seconds")

COMPANY_COLOR = {
    # SpaceX — 깊은 네이비 (브랜드)
    "SpaceX": "#005288",
    "spacex": "#005288",
    # Rocket Lab — 짙은 주황 (브랜드 인접, 채도 ↑)
    "Rocket Lab": "#CC5500",
    "Rocket Lab Ltd": "#CC5500",
    "rocketlab": "#CC5500",
    # Blue Origin — Vivid Royal Blue (네이비와 구분되도록 채도 ↑)
    "Blue Origin": "#3D7DE8",
    "blueorigin": "#3D7DE8",
    # Firefly — Amber (반딧불이, Rocket Lab 의 짙은 주황과 구분되도록 노란 톤)
    "Firefly Aerospace": "#FFB300",
    "Firefly": "#FFB300",
    "firefly": "#FFB300",
}
DEFAULT_COLOR = "#666666"

AXIS_LABEL = {
    "territory": "영토&자원",
    "comms": "통신",
    "energy_logistics": "에너지&물류",
    "defense": "군사&안보",
}
AXIS_COLOR = {
    "territory":         "#8B5CF6",
    "comms":             "#10B981",
    "energy_logistics":  "#F59E0B",
    "defense":           "#EF4444",
}


def latest(pattern: str) -> Path | None:
    today_match = sorted(RAW_DIR.glob(pattern.replace("*", TODAY, 1)))
    if today_match:
        return today_match[-1]
    files = sorted(RAW_DIR.glob(pattern))
    return files[-1] if files else None


def load(path: Path | None) -> dict:
    if not path:
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_curated_missions() -> list[dict]:
    """data/rocket/missions.json (사람이 큐레이션하는 정적 미션 목록) 로드.
    파일 없으면 경고 출력 후 빈 배열 반환."""
    p = ROOT / "data" / "missions.json"
    if not p.exists():
        print(f"⚠ missions.json 없음 ({p.relative_to(ROOT)}) — Mission Insight 가 빈 상태로 표시됨")
        return []
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def load_curated_rocket_specs(metrics: dict) -> list[dict]:
    """data/rocket/rocket_specs.json + metrics(by_company / by_model) 동적 매핑.
    launches/successRate 가 metrics 에서 발견되면 덮어쓰고, 못 찾으면 정적값 유지."""
    p = ROOT / "data" / "rocket_specs.json"
    if not p.exists():
        print(f"⚠ rocket_specs.json 없음 ({p.relative_to(ROOT)}) — Silhouette/버블 차트가 빈 상태로 표시됨")
        return []
    with p.open(encoding="utf-8") as f:
        specs = json.load(f)
    # 분석기 산출: by_model 은 list[{company, model, total, success, failure, partial, rate_pct}]
    by_model_list = ((metrics.get("success_rate") or {}).get("by_model") or [])
    by_model = {}
    if isinstance(by_model_list, list):
        for it in by_model_list:
            mname = (it or {}).get("model")
            if mname:
                by_model[mname] = it
    elif isinstance(by_model_list, dict):
        by_model = by_model_list
    by_company = ((metrics.get("success_rate") or {}).get("by_company") or {})
    # JSON 의 모델명 → 분석기의 by_model 키로 alias 매핑 (B5 변형명 등)
    MODEL_ALIAS = {
        "Falcon 9 B5":  "Falcon 9",
        "Starship V3":  "Starship",
        "Alpha":        "Firefly Alpha",
    }
    NAME_TO_COMPANY = {
        "Starship V3":  "spacex",
        "Falcon Heavy": "spacex",
        "Falcon 9 B5":  "spacex",
        "Electron":     "rocketlab",
        "Neutron":      "rocketlab",
        "New Glenn":    "blueorigin",
        "New Shepard":  "blueorigin",
        "Alpha":        "firefly",
    }
    for s in specs:
        name = s.get("name", "")
        key = MODEL_ALIAS.get(name, name)
        m = by_model.get(key)
        # 1순위: by_model 직접 매치
        if isinstance(m, dict) and m.get("total"):
            s["launches"] = m.get("total") or s.get("launches")
            rate = m.get("rate_pct")
            if rate is None and m.get("total"):
                rate = round((m.get("success", 0) / m["total"]) * 100, 1)
            if rate is not None:
                s["successRate"] = rate
            continue
        # 2순위: by_company 폴백 (정적값이 비어있을 때만)
        slug = NAME_TO_COMPANY.get(name)
        c = by_company.get(slug) if slug else None
        # 0 은 "아직 발사 안 함" 의 유효값이므로 None 일 때만 fallback
        if isinstance(c, dict) and c.get("total") and s.get("launches") is None:
            s["launches"] = c.get("total")
            if s.get("successRate") is None and c.get("total"):
                s["successRate"] = round((c.get("success", 0) / c["total"]) * 100, 1)
    return specs


def color_for(company: str | None) -> str:
    if not company:
        return DEFAULT_COLOR
    for k, v in COMPANY_COLOR.items():
        if k.lower() in company.lower():
            return v
    return DEFAULT_COLOR


def classify_net_precision(rec: dict) -> dict:
    """LL2 의 net 필드 정밀도를 분류해 표시·정렬·필터링용 메타를 부여한다.

    LL2 는 일정 미상인 발사를 'YYYY-MM-DD T00:00:00Z' 형태로 분기/년 말에 더미 적재한다
    (예: 2026-12-31 00:00 113건). 사용자에게 confirmed 와 동일하게 보이지 않게 분리한다.

    precision: confirmed (분 단위) / day (일 단위 미정 시간) / month / quarter / year / unknown
    label: 한국어 표기 ('확정', '월 미정', '연 미정' 등)
    sortable: ISO 정렬 키 (정밀도 무관, raw net 사용)
    """
    net = rec.get("net") or ""
    status = rec.get("status") or {}
    abbrev = str(status.get("abbrev") or "").lower()
    sname = str(status.get("name") or "").lower()
    is_tbd = abbrev in ("tbd", "tbc") or "to be determined" in sname or "to be confirmed" in sname or "hold" in sname
    if not net:
        return {"precision": "unknown", "label": "미정", "sortable": "9999-12-31T23:59:59Z"}
    head = net[:19]  # YYYY-MM-DDTHH:MM:SS
    date_part = head[:10]
    time_part = head[11:19] if len(head) >= 19 else "00:00:00"
    is_midnight = time_part == "00:00:00"
    yyyy = date_part[:4]
    mm = date_part[5:7]
    dd = date_part[8:10]
    quarter_ends = {("03", "31"), ("06", "30"), ("09", "30"), ("12", "31")}
    month_last_days = {("01", "31"), ("02", "28"), ("02", "29"), ("03", "31"), ("04", "30"),
                       ("05", "31"), ("06", "30"), ("07", "31"), ("08", "31"), ("09", "30"),
                       ("10", "31"), ("11", "30"), ("12", "31")}
    if is_tbd and is_midnight:
        if (mm, dd) == ("12", "31"):
            return {"precision": "year", "label": f"{yyyy}년 (연 미정)", "sortable": net}
        if (mm, dd) in quarter_ends:
            qlabel = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}[mm]
            return {"precision": "quarter", "label": f"{yyyy}-{qlabel} (분기 미정)", "sortable": net}
        if (mm, dd) in month_last_days:
            return {"precision": "month", "label": f"{yyyy}-{mm} (월 미정)", "sortable": net}
        return {"precision": "day", "label": f"{date_part} (시간 미정)", "sortable": net}
    if is_tbd:
        return {"precision": "day", "label": f"{date_part} {time_part[:5]} (잠정)", "sortable": net}
    return {"precision": "confirmed", "label": f"{date_part} {time_part[:5]} UTC", "sortable": net}


def annotate_records(records: list[dict]) -> list[dict]:
    """records 에 net_precision/net_label/net_sortable 필드를 in-place 주입."""
    for r in records:
        info = classify_net_precision(r)
        r["net_precision"] = info["precision"]
        r["net_label"] = info["label"]
        r["net_sortable"] = info["sortable"]
    return records


def filter_upcoming_90d(records: list[dict]) -> list[dict]:
    """향후 90일 confirmed/잠정 발사. 미정(month/quarter/year) 은 별도 버킷으로 분리."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=90)
    floor = datetime.now(timezone.utc) - timedelta(days=1)
    out = []
    for r in records:
        net = r.get("net")
        if not net:
            continue
        try:
            dt = datetime.fromisoformat(net.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (floor <= dt <= cutoff):
            continue
        # 분기/연 단위 미정은 90일 윈도우 안에 들어가도 캘린더 시각화에서 제외
        # (Tabulator 표 에서는 보존, _in_calendar 플래그로 구분)
        in_cal = r.get("net_precision") in ("confirmed", "day")
        out.append({**r, "_dt": dt.isoformat(), "_in_calendar": in_cal})
    out.sort(key=lambda r: r["_dt"])
    return out


def split_indeterminate(records: list[dict]) -> list[dict]:
    """월/분기/연 단위 미정 레코드만 따로 모음."""
    return [r for r in records if r.get("net_precision") in ("month", "quarter", "year", "unknown")]


def main() -> int:
    print(f"🎨 generate_rocket_dashboard.py ({NOW_ISO})")

    metrics_path = latest("*_analysis_TSR_rocket_metrics.json")
    upcoming_path = latest("*_upcoming_launchlibrary2_launches.json")
    specs_path = latest("*_specs_launchlibrary2_rockets.json")
    curated_path = latest("*_specs_user_curated_rockets.json")
    history_paths = {
        "spacex": latest("*_history_spacex_launches.json"),
        "rocketlab": latest("*_history_rocketlab_launches.json"),
        "blueorigin": latest("*_history_blueorigin_launches.json"),
        "firefly": latest("*_history_firefly_launches.json"),
    }

    if not metrics_path:
        print("❌ analyze_rocket_data.py 산출물(*_analysis_TSR_rocket_metrics.json) 없음.", file=sys.stderr)
        return 1

    metrics = load(metrics_path).get("metrics", {})
    curated_missions = load_curated_missions()
    curated_rocket_specs = load_curated_rocket_specs(metrics)
    upcoming = load(upcoming_path).get("records", []) if upcoming_path else []

    # ── 무결성 검증: 데이터 stale 여부 (24h+ 이전 fetch 면 경고) ──
    data_stale = False
    stale_age_hours = None
    if upcoming_path and upcoming_path.exists():
        upcoming_meta = load(upcoming_path).get("_meta", {})
        fetched_at = upcoming_meta.get("fetched_at")
        if fetched_at:
            try:
                from datetime import datetime as _dt
                fetched_dt = _dt.fromisoformat(fetched_at.replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - fetched_dt
                stale_age_hours = round(age.total_seconds() / 3600, 1)
                if stale_age_hours > 24:
                    data_stale = True
                    print(f"⚠ 무결성 경고: upcoming 데이터 {stale_age_hours}h 전 fetch — 24h 초과 stale",
                          file=sys.stderr)
            except (ValueError, TypeError):
                pass
    integrity = {
        "data_stale": data_stale,
        "stale_age_hours": stale_age_hours,
        "upcoming_count": len(upcoming),
        "history_count": None,  # 아래 history 빌드 후 채움
    }
    annotate_records(upcoming)
    upcoming_90 = filter_upcoming_90d(upcoming)
    upcoming_indeterminate = split_indeterminate(upcoming)
    specs = load(specs_path).get("records", []) if specs_path else []

    # User-curated specs (from data/raw/*_specs_user_curated_*.json)
    curated_doc = load(curated_path) if curated_path else {}
    curated_specs = curated_doc.get("rocket_specs", [])
    curated_meta = curated_doc.get("_meta", {})
    curated_quality = curated_doc.get("data_quality_notes", {})

    history_records: list[dict] = []
    for slug, p in history_paths.items():
        if not p:
            continue
        for r in load(p).get("records", []):
            history_records.append({**r, "_slug": slug})
    annotate_records(history_records)
    integrity["history_count"] = len(history_records)

    # 메타
    raw_files_meta = []
    for label, p in [
        ("upcoming", upcoming_path),
        ("spacex history", history_paths["spacex"]),
        ("rocketlab history", history_paths["rocketlab"]),
        ("blueorigin history", history_paths["blueorigin"]),
        ("firefly history", history_paths["firefly"]),
        ("rocket specs (LL2)", specs_path),
        ("analysis metrics", metrics_path),
        ("curated specs (user URLs)", curated_path),
    ]:
        if p:
            data = load(p)
            meta = data.get("_meta", {})
            raw_files_meta.append({
                "label": label,
                "file": p.name,
                "fetched_at": meta.get("fetched_at"),
                "source": meta.get("source"),
                "reliability": meta.get("reliability"),
                "record_count": meta.get("record_count") or len(data.get("records", [])),
            })

    # JS 로 넘길 페이로드
    payload = {
        "now": NOW_ISO,
        "metrics": metrics,
        "upcoming_90": upcoming_90,
        "upcoming_indeterminate": upcoming_indeterminate,
        "specs": specs,
        "curated_specs": curated_specs,
        "curated_meta": curated_meta,
        "curated_quality": curated_quality,
        "missions": curated_missions,
        "rocket_specs_curated": curated_rocket_specs,
        "integrity": integrity,
        "history": history_records,
        "raw_meta": raw_files_meta,
        "company_colors": COMPANY_COLOR,
        "default_color": DEFAULT_COLOR,
        "axis_label": AXIS_LABEL,
        "axis_color": AXIS_COLOR,
    }

    # 데이터를 별도 JSON 으로 분리 (fetch 로딩용) + HTML 에는 fallback 으로만 inline
    data_out = EXPORTS_DIR / "rocket_dashboard_data.json"
    with data_out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)

    html = build_html(payload)
    out = EXPORTS_DIR / "rocket_dashboard.html"
    out.write_text(html, encoding="utf-8")

    size_kb = out.stat().st_size / 1024
    data_kb = data_out.stat().st_size / 1024
    print(f"✅ 대시보드 생성: {out.relative_to(ROOT)} ({size_kb:,.1f} KB)")
    print(f"   데이터 파일:    {data_out.relative_to(ROOT)} ({data_kb:,.1f} KB)")
    print(f"   브라우저로 열기:  open '{out}'")
    return 0


def build_html(payload: dict) -> str:
    # 페이로드는 별도 JSON (rocket_dashboard_data.json) 으로 fetch — HTML inline 제거됨
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>🚀 Rocket Launch Intelligence — TSR</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="./vendor/tabulator_midnight.min.css" rel="stylesheet">
<script type="text/javascript" src="./vendor/tabulator.min.js"></script>
<style>
  :root {{
    --bg:#0d1117; --card:#161b22; --line:#30363d; --text:#e6edf3; --muted:#8b949e;
    --accent:#58a6ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "Pretendard", "Apple SD Gothic Neo", sans-serif;
    font-size: 14px; line-height: 1.5;
  }}
  header {{
    padding: 18px 28px; border-bottom: 1px solid var(--line);
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;
  }}
  header h1 {{ margin: 0; font-size: 20px; font-weight: 600; }}
  header .meta {{ color: var(--muted); font-size: 12px; }}
  .axis-legend {{ display:flex; gap:10px; flex-wrap:wrap; font-size:12px; }}
  .axis-legend span {{ padding:3px 9px; border-radius:99px; }}
  .countdown {{
    background: var(--card); border:1px solid var(--line); border-radius: 10px;
    padding: 10px 18px; display:inline-flex; gap:14px; align-items:center;
    font-size: 13px;
  }}
  .countdown b {{ color: var(--accent); font-size: 16px; }}
  nav.tabs {{
    display: flex; gap: 4px; padding: 0 24px; border-bottom: 1px solid var(--line);
    background: var(--bg); position: sticky; top: 0; z-index: 10;
  }}
  nav.tabs button {{
    background: transparent; color: var(--muted); border: none;
    padding: 12px 18px; cursor: pointer; font-size: 14px;
    border-bottom: 2px solid transparent;
  }}
  nav.tabs button.active {{ color: var(--text); border-bottom-color: var(--accent); }}
  nav.tabs button:hover {{ color: var(--text); }}
  main {{ padding: 24px 28px; }}
  .tab {{ display: none; }}
  .tab.active {{ display: block; }}
  .card {{
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 18px; margin-bottom: 16px;
  }}
  .card h2 {{ margin: 0 0 12px 0; font-size: 16px; }}
  .grid {{ display: grid; gap: 16px; }}
  .grid.cols-3 {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
  .grid.cols-2 {{ grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }}
  .rocket-card {{
    background: #0b1018; border:1px solid var(--line); border-radius: 10px; padding: 14px;
  }}
  .rocket-card h3 {{ margin: 0 0 6px 0; font-size: 15px; }}
  .rocket-card .row {{ display:flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin: 2px 0; }}
  .rocket-card .row b {{ color: var(--text); font-weight: 500; }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px;
    background: #2a3340; color: var(--text);
  }}
  .badge.green {{ background: #1f6f3a; }}
  .badge.red   {{ background: #8b1a1a; }}
  .badge.amber {{ background: #8a6d20; }}
  .badge.gray  {{ background: #2a3340; }}
  table.simple {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  table.simple th, table.simple td {{ padding: 8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  table.simple th {{ color: var(--muted); font-weight: 500; }}
  .reliab-1   {{ color:#7ee787; }}
  .reliab-15  {{ color:#a5d6ff; }}
  .reliab-2   {{ color:#ffa657; }}
  .reliab-3   {{ color:#f78166; }}
  .muted {{ color: var(--muted); }}

  /* ── Countdown LIVE pulse ──────────────────────────────────────── */
  @keyframes livePulse {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0.2; }}
  }}
  .live-dot {{
    width: 7px; height: 7px;
    border-radius: 50%;
    background: #fff;
    animation: livePulse 1.5s infinite;
    display: inline-block;
  }}

  /* ── Data Quality footer + badges (dq- prefix) ─────────────────── */
  body {{ padding-bottom: 44px; }} /* 고정 푸터 공간 */
  .dq-footer {{
    position: fixed; bottom: 0; left: 0; right: 0; width: 100%;
    background: #080d14; border-top: 1px solid var(--line);
    padding: 8px 20px; z-index: 1000;
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; font-size: 11px; color: var(--muted); flex-wrap: wrap;
  }}
  .dq-disclaimer {{ flex: 1 1 auto; line-height: 1.5; }}
  .dq-disclaimer b {{ color: #ffa657; font-weight: 500; }}
  .dq-footer-meta {{ flex: 0 0 auto; color: var(--muted); white-space: nowrap; }}
  .dq-footer-meta b {{ color: var(--text); font-weight: 500; }}

  .dq-badge {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 10px; border-radius: 99px;
    font-size: 11px; font-weight: 600; cursor: help;
    margin-left: 10px; vertical-align: middle;
    transition: filter 0.15s ease;
    user-select: none;
  }}
  .dq-badge:hover {{ filter: brightness(1.15); }}
  .dq-badge .dq-dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: 0.7; }}
  .dq-badge.dq-A {{ background: #E1F5EE; color: #0F6E56; }}
  .dq-badge.dq-B {{ background: #E6F1FB; color: #185FA5; }}
  .dq-badge.dq-C {{ background: #FAEEDA; color: #854F0B; }}
  .dq-badge.dq-D {{ background: #F1EFE8; color: #5F5E5A; }}
  .dq-tooltip {{
    position: fixed; pointer-events: none;
    background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 9px 12px; font-size: 12px; line-height: 1.5;
    max-width: 320px; min-width: 180px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.55);
    z-index: 9999; opacity: 0; visibility: hidden;
    transition: opacity 0.12s ease;
  }}
  .dq-tooltip.dq-visible {{ opacity: 1; visibility: visible; }}
  .dq-tooltip-title {{ font-weight: 600; padding-bottom: 4px; margin-bottom: 4px; border-bottom: 1px solid var(--line); }}
  .dq-tooltip-source {{ color: var(--muted); font-size: 11px; }}

  /* ── Canvas Viz tooltip (cv- prefix) ──────────────────────────── */
  .cv-tooltip {{
    position: fixed; pointer-events: none;
    background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 10px 12px; font-size: 12px; line-height: 1.55;
    max-width: 320px; min-width: 180px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.55);
    z-index: 9999; opacity: 0; visibility: hidden;
    transition: opacity 0.12s ease;
  }}
  .cv-tooltip.cv-visible {{ opacity: 1; visibility: visible; }}
  .cv-tooltip-title {{ font-weight: 600; font-size: 13px; padding-bottom: 6px; border-bottom: 1px solid var(--line); margin-bottom: 6px; }}
  .cv-tooltip-row {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin: 2px 0; }}
  .cv-tooltip-row span:first-child {{ color: var(--muted); }}

  /* ── 4축 AI 해석 카드 ─────────────────────────────────────────── */
  #axisInsightCard {{ display: flex; flex-direction: column; }}
  .ai-headline {{
    font-size: 14px; font-weight: 600; color: var(--text);
    padding: 10px 12px; margin-bottom: 12px;
    background: linear-gradient(90deg, rgba(88,166,255,0.15), transparent);
    border-left: 3px solid var(--accent); border-radius: 4px;
  }}
  .ai-headline b {{ color: var(--accent); }}
  .ai-section {{ margin-bottom: 14px; }}
  .ai-section h3 {{
    font-size: 13px; font-weight: 600; margin: 0 0 6px 0; color: var(--text);
    display: flex; align-items: center; gap: 6px;
  }}
  .ai-section p {{ font-size: 13px; line-height: 1.6; color: var(--text); margin: 0; }}
  .ai-secondary {{
    font-size: 12px; color: var(--muted); padding: 8px 10px;
    background: #0b1018; border-radius: 6px; border: 1px solid var(--line);
    margin-top: 6px;
  }}
  .ai-secondary b {{ color: var(--text); }}
  .ai-axis-rank {{
    display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px;
    padding-top: 10px; border-top: 1px dashed var(--line);
  }}
  .ai-axis-rank-item {{
    font-size: 11px; padding: 3px 9px; border-radius: 99px;
    color: #fff; font-weight: 500;
  }}

  /* ── Mission Insight (mi- prefix) ───────────────────────────────── */
  #mi-section {{ display: grid; gap: 16px; }}
  .mi-tabs {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
  .mi-tab {{
    padding: 6px 14px; border-radius: 99px; font-size: 13px;
    background: transparent; color: var(--muted); border: 1px solid var(--line);
    cursor: pointer; transition: all 0.15s ease;
  }}
  .mi-tab:hover {{ color: var(--text); border-color: var(--accent); }}
  .mi-tab.mi-active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .mi-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
  .mi-card {{
    background: #0b1018; border: 1px solid var(--line); border-radius: 10px;
    padding: 14px 16px; cursor: pointer;
    transition: transform 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease;
    display: flex; flex-direction: column; gap: 8px;
  }}
  .mi-card:hover {{ transform: translateY(-2px); border-color: var(--accent); box-shadow: 0 6px 18px rgba(0,0,0,0.45); }}
  .mi-card.mi-card-active {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(88,166,255,0.4); }}
  .mi-sector-pill {{
    display: inline-block; padding: 2px 10px; border-radius: 99px;
    font-size: 11px; font-weight: 600; align-self: flex-start;
  }}
  .mi-name {{ font-size: 16px; font-weight: 500; color: var(--text); line-height: 1.3; }}
  .mi-summary {{
    font-size: 12px; color: var(--muted); line-height: 1.45;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; min-height: 34px;
  }}
  .mi-divider {{ border-top: 1px solid var(--line); margin: 2px 0; }}
  .mi-meta {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; }}
  .mi-meta-row {{ display: flex; gap: 6px; align-items: center; color: var(--muted); }}
  .mi-meta-row b {{ color: var(--text); font-weight: 500; }}
  .mi-status-pill {{
    display: inline-block; padding: 2px 8px; border-radius: 99px;
    font-size: 10px; font-weight: 600; margin-left: auto;
  }}
  .mi-status-확정       {{ background: #1f6f3a; color: #fff; }}
  .mi-status-진행 {{ background: #0c447c; color: #fff; }}
  .mi-status-진행중      {{ background: #0c447c; color: #fff; }}
  .mi-status-예정       {{ background: #8a6d20; color: #fff; }}
  .mi-status-개발 {{ background: #5a3a8a; color: #fff; }}
  .mi-status-개발중      {{ background: #5a3a8a; color: #fff; }}

  /* Timeline */
  .mi-timeline-wrap {{ width: 100%; }}
  .mi-timeline {{ width: 100%; height: 160px; display: block; overflow: visible; }}
  .mi-axis-line {{ stroke: var(--line); stroke-width: 1; }}
  .mi-tick {{ stroke: var(--line); stroke-width: 1; }}
  .mi-tick-label {{ fill: var(--muted); font-size: 11px; font-family: -apple-system, "Pretendard", sans-serif; }}
  .mi-today-line {{ stroke: #ff6b6b; stroke-width: 1; stroke-dasharray: 4 4; }}
  .mi-today-label {{ fill: #ff6b6b; font-size: 11px; font-weight: 600; font-family: -apple-system, sans-serif; }}
  .mi-marker {{ cursor: pointer; transition: transform 0.15s ease; }}
  .mi-marker:hover {{ filter: brightness(1.2); }}
  .mi-marker-active {{ animation: mi-pulse 1.2s ease-in-out infinite; }}
  @keyframes mi-pulse {{
    0%, 100% {{ transform: scale(1); }}
    50% {{ transform: scale(1.6); }}
  }}
  .mi-label {{ fill: var(--text); font-size: 10px; font-family: -apple-system, "Pretendard", sans-serif; pointer-events: none; }}
  .mi-tooltip {{
    position: fixed; pointer-events: none;
    background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 10px 12px; font-size: 12px; line-height: 1.55;
    max-width: 320px; min-width: 200px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.55);
    z-index: 9999; opacity: 0; visibility: hidden;
    transition: opacity 0.12s ease;
  }}
  .mi-tooltip.mi-visible {{ opacity: 1; visibility: visible; }}
  .mi-tooltip-title {{ font-weight: 600; font-size: 13px; padding-bottom: 6px; border-bottom: 1px solid var(--line); margin-bottom: 6px; }}
  .mi-tooltip-row {{ display: flex; justify-content: space-between; gap: 12px; margin: 2px 0; }}
  .mi-tooltip-row span:first-child {{ color: var(--muted); }}

  /* ── Rocket Silhouette (rs- prefix) ─────────────────────────────── */
  .rs-stage {{
    display: grid; grid-auto-flow: column; grid-auto-columns: 28px;
    column-gap: 28px; align-items: end;
    height: 220px; padding: 0 16px;
    border-bottom: 0.5px solid var(--line);
    position: relative; overflow: visible;
  }}
  .rs-col {{ display: flex; flex-direction: column; align-items: center; }}
  .rs-leo-tag {{
    font-size: 10px; color: var(--muted); white-space: nowrap;
    padding-bottom: 4px; min-height: 14px; text-align: center;
  }}
  .rs-bar {{
    width: 28px;
    border-top-left-radius: 14px; border-top-right-radius: 14px;
    cursor: pointer; position: relative; overflow: hidden;
    transition: outline 0.15s ease, filter 0.15s ease;
  }}
  .rs-bar:hover {{
    outline: 2px solid var(--accent); outline-offset: 2px;
    z-index: 5; filter: brightness(1.15);
  }}
  .rs-stage-seg {{
    position: absolute; left: 0; right: 0;
    background-color: var(--seg-color, currentColor);
  }}
  /* 재사용 단: 풀 채도 */
  .rs-stage-seg.rs-reuse {{ opacity: 1.0; }}
  /* 소모 단: 24% 검정 dim + 70% 흰색 대각선 줄무늬 (다중 background-image 레이어) */
  .rs-stage-seg.rs-expend {{
    background-image:
      linear-gradient(rgba(0,0,0,0.24), rgba(0,0,0,0.24)),
      repeating-linear-gradient(45deg,
        rgba(255,255,255,0.78) 0 2.5px,
        transparent 2.5px 6.5px);
  }}
  .rs-stage-divider {{
    position: absolute; left: 0; right: 0; height: 1px;
    background: rgba(0,0,0,0.65); pointer-events: none;
  }}
  /* 솔리드 / 줄무늬 의미 범례 (실루엣 카드 상단) */
  .rs-legend {{
    display: flex; gap: 18px; flex-wrap: wrap; align-items: center;
    margin-bottom: 10px; padding: 8px 12px;
    background: #0b1018; border: 1px solid var(--line); border-radius: 8px;
    font-size: 12px;
  }}
  .rs-legend-item {{ display: inline-flex; align-items: center; gap: 6px; color: var(--text); }}
  .rs-legend-item.muted {{ color: var(--muted); }}
  .rs-legend-swatch {{
    display: inline-block; width: 22px; height: 14px; border-radius: 3px;
    background-color: #58a6ff; position: relative; overflow: hidden;
    border: 1px solid rgba(255,255,255,0.15);
  }}
  .rs-legend-swatch.rs-legend-solid {{ opacity: 1.0; }}
  .rs-legend-swatch.rs-legend-stripe {{
    background-image:
      linear-gradient(rgba(0,0,0,0.24), rgba(0,0,0,0.24)),
      repeating-linear-gradient(45deg,
        rgba(255,255,255,0.78) 0 2.5px,
        transparent 2.5px 6.5px);
  }}

  /* 단별 재사용 미니 범례 (이름 위, 우측 상단 코너에 작은 dot) — 옵션, 카드 하단에 표기 */
  .rs-stage-legend {{
    display: flex; gap: 4px; align-items: center; justify-content: center;
    margin-top: 2px; flex-wrap: wrap;
  }}
  .rs-stage-dot {{
    width: 8px; height: 8px; border-radius: 2px; display: inline-block;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.2);
  }}
  .rs-stage-dot.rs-expend {{
    background-image:
      linear-gradient(rgba(0,0,0,0.24), rgba(0,0,0,0.24)),
      repeating-linear-gradient(45deg,
        rgba(255,255,255,0.85) 0 1px, transparent 1px 2.5px);
  }}
  .rs-foot-row {{
    display: grid; grid-auto-flow: column; grid-auto-columns: 28px;
    column-gap: 28px; padding: 8px 16px 0;
  }}
  .rs-foot-col {{ display: flex; flex-direction: column; align-items: center; gap: 1px; }}
  .rs-name {{ font-size: 12px; color: var(--text); text-align: center; line-height: 1.2; max-width: 90px; }}
  .rs-h {{ font-size: 10px; color: var(--muted); }}
  .rs-tooltip {{
    position: fixed; pointer-events: none;
    background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 10px 12px; font-size: 12px; line-height: 1.55;
    max-width: 320px; min-width: 200px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.55);
    z-index: 9999; opacity: 0; visibility: hidden;
    transition: opacity 0.12s ease;
  }}
  .rs-tooltip.rs-visible {{ opacity: 1; visibility: visible; }}
  .rs-tooltip-title {{ font-weight: 600; font-size: 13px; padding-bottom: 6px; border-bottom: 1px solid var(--line); margin-bottom: 6px; }}
  .rs-tooltip-row {{ display: flex; justify-content: space-between; gap: 12px; }}
  .rs-tooltip-row span:first-child {{ color: var(--muted); }}

  /* ── Bubble Chart (bc- prefix) ─────────────────────────────────── */
  .bc-wrap {{ position: relative; width: 100%; height: 260px; }}
  .bc-canvas {{ width: 100%; height: 260px; display: block; cursor: crosshair; }}
  .bc-hidden-list {{
    margin-top: 10px; font-size: 11px; color: var(--muted);
    border-top: 1px dashed var(--line); padding-top: 8px;
  }}
  .bc-hidden-list b {{ color: var(--text); font-weight: 500; }}
  .bc-hidden-pill {{
    display: inline-block; padding: 2px 8px; margin: 0 4px 4px 0;
    border-radius: 99px; background: #2a3340; font-size: 11px;
  }}

  /* ── Density Heatmap (hm- prefix; isolated from other tabs) ─────── */
  .hm-container {{ padding: 4px 0; width: 100%; }}
  .hm-scroll {{ width: 100%; padding-bottom: 8px; }}
  .hm-grid {{ display: grid; gap: 3px; align-items: center; width: 100%; }}
  .hm-row-label {{
    font-size: 11px; color: var(--text); padding: 4px 10px;
    text-align: right; white-space: nowrap; font-weight: 500;
  }}
  .hm-col-header {{
    font-size: 10px; color: var(--muted); padding: 4px 2px;
    text-align: center; white-space: nowrap; font-weight: 500;
    overflow: hidden; text-overflow: ellipsis;
  }}
  .hm-corner {{ padding: 4px 10px; }}
  .hm-cell {{
    height: 26px; min-width: 0; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700;
    background: #1a2030; color: transparent;
    border: 1px solid var(--line);
    position: relative;
    transition: transform 0.15s ease, outline 0.15s ease, box-shadow 0.15s ease;
  }}
  .hm-cell.hm-zero {{ color: transparent; }}
  .hm-cell.hm-1 {{ background: #B5D4F4; color: #0C447C; cursor: pointer; border-color: #B5D4F4; }}
  .hm-cell.hm-2 {{ background: #378ADD; color: #ffffff; cursor: pointer; border-color: #378ADD; }}
  .hm-cell.hm-3 {{ background: #0C447C; color: #ffffff; cursor: pointer; border-color: #0C447C; }}
  /* 미정 일정 (best-guess 매핑): 대각선 줄무늬 + 점선 테두리 + ⚠ 마커 */
  .hm-cell.hm-uncertain {{
    background-image: repeating-linear-gradient(45deg,
      rgba(255,255,255,0.18) 0 3px, transparent 3px 7px);
    border-style: dashed;
  }}
  .hm-cell.hm-uncertain-only {{
    background: #2a2a3a; color: #d0bda0; cursor: pointer; border-color: #6a5a3a;
    background-image: repeating-linear-gradient(45deg,
      rgba(212,165,90,0.22) 0 3px, transparent 3px 7px);
  }}
  .hm-cell.hm-uncertain::after, .hm-cell.hm-uncertain-only::after {{
    content: "⚠"; position: absolute; top: -3px; right: -1px;
    font-size: 9px; color: #ffd479; text-shadow: 0 0 2px rgba(0,0,0,0.85);
    pointer-events: none;
  }}
  .hm-cell:not(.hm-zero):hover {{
    transform: scale(1.12);
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    z-index: 5;
    box-shadow: 0 6px 20px rgba(0,0,0,0.55);
  }}
  .hm-legend {{
    display: flex; gap: 18px; align-items: center;
    margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--line);
    font-size: 12px; color: var(--muted); flex-wrap: wrap;
  }}
  .hm-legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .hm-legend-swatch {{
    display: inline-block; width: 18px; height: 18px;
    border-radius: 4px; border: 1px solid var(--line);
  }}
  .hm-tooltip {{
    position: fixed; pointer-events: auto;
    background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 10px;
    padding: 0; font-size: 12px; line-height: 1.5;
    max-width: 420px; min-width: 280px;
    max-height: 70vh; display: flex; flex-direction: column;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
    z-index: 9999; opacity: 0;
    transition: opacity 0.12s ease;
    left: 0; top: 0;
    visibility: hidden;
  }}
  .hm-tooltip.hm-visible {{ opacity: 1; visibility: visible; }}
  .hm-tooltip-header {{
    font-weight: 600; font-size: 13px;
    padding: 12px 14px 8px; border-bottom: 1px solid var(--line);
    flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  }}
  .hm-tooltip-body {{
    overflow-y: auto; padding: 4px 14px 12px;
    /* 스크롤 영역에서 mouseover 가 cell 로 bubble 안 되도록 격리 */
    scrollbar-width: thin;
  }}
  .hm-tooltip-body::-webkit-scrollbar {{ width: 8px; }}
  .hm-tooltip-body::-webkit-scrollbar-thumb {{ background: var(--line); border-radius: 4px; }}
  .hm-tooltip-pin {{
    cursor: pointer; padding: 2px 8px; border-radius: 6px; font-size: 10px;
    background: #2a3340; color: var(--muted); border: 1px solid var(--line);
    user-select: none; transition: all 0.15s ease;
  }}
  .hm-tooltip-pin:hover {{ background: #3a4350; color: var(--text); }}
  .hm-tooltip-pin.hm-pinned {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .hm-tooltip-scrollcue {{
    position: absolute; bottom: 6px; left: 50%; transform: translateX(-50%);
    font-size: 10px; color: var(--muted); pointer-events: none;
    background: rgba(22,27,34,0.92); padding: 1px 8px; border-radius: 99px;
  }}
  .hm-launch-item {{ padding: 8px 0; border-bottom: 1px dashed var(--line); }}
  .hm-launch-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .hm-launch-row {{ display: flex; gap: 8px; margin: 2px 0; align-items: baseline; }}
  .hm-launch-label {{ color: var(--muted); min-width: 56px; font-size: 11px; }}
  .hm-launch-value {{ font-weight: 500; flex: 1; word-break: break-word; }}
  .hm-status-pill {{
    display: inline-block; padding: 2px 8px; border-radius: 99px;
    font-size: 10px; font-weight: 600; line-height: 1.4;
  }}
  .hm-status-confirmed {{ background: #E1F5EE; color: #0F6E56; }}
  .hm-status-tentative {{ background: #FAEEDA; color: #854F0B; }}
  .hm-status-tbd       {{ background: #F1EFE8; color: #5F5E5A; }}
</style>
</head>
<body>

<header>
  <div>
    <h1>🚀 Rocket Launch Intelligence</h1>
    <div class="meta">last update: <span id="lastUpdate"></span> · TSR / trading-space-research</div>
  </div>
  <div class="axis-legend" title="CORE_CONCEPT.md 4축 프레임워크">
    <span style="background:#8B5CF6">영토&amp;자원</span>
    <span style="background:#10B981">통신</span>
    <span style="background:#F59E0B">에너지&amp;물류</span>
    <span style="background:#EF4444">군사&amp;안보</span>
  </div>
  <div class="countdown" id="countdown">다음 발사 로딩…</div>
</header>

<nav class="tabs" id="tabs">
  <button class="active" data-tab="t-cal">📅 발사 캘린더</button>
  <button data-tab="t-spec">🔭 로켓 스펙</button>
  <button data-tab="t-hist">📜 발사 이력 (UTC)</button>
  <button data-tab="t-stock">📈 인사이트</button>
</nav>

<main>

  <section id="t-cal" class="tab active">
    <div class="card">
      <h2>📅 발사 밀도 히트맵 <span class="muted" style="font-size:12px">— 주요 10개 기업 × 발사 주차 (월요일 기준), 그 외 기업은 ↓ 예정 발사 표 참조</span><span class="dq-badge dq-B" data-dq="B" data-dq-source="Launch Library 2 (독립 검증 플랫폼)"><span class="dq-dot"></span>Grade B · 검증됨</span></h2>
      <div class="hm-container">
        <div class="hm-scroll"><div id="hmCalendar"></div></div>
        <div class="hm-legend">
          <span class="hm-legend-item"><span class="hm-legend-swatch" style="background:#1a2030"></span>0회 (없음)</span>
          <span class="hm-legend-item"><span class="hm-legend-swatch" style="background:#B5D4F4;border-color:#B5D4F4"></span>1회</span>
          <span class="hm-legend-item"><span class="hm-legend-swatch" style="background:#378ADD;border-color:#378ADD"></span>2회</span>
          <span class="hm-legend-item"><span class="hm-legend-swatch" style="background:#0C447C;border-color:#0C447C"></span>3회+</span>
          <span class="hm-legend-item"><span class="hm-legend-swatch" style="background:#2a2a3a;border-color:#6a5a3a;border-style:dashed;background-image:repeating-linear-gradient(45deg,rgba(212,165,90,0.4) 0 3px, transparent 3px 7px)"></span>⚠ 미정 (재확인 필요, best-guess 매핑)</span>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>예정 발사 (≤ 90일) <span class="muted" style="font-size:12px">— 일본시간 (JST, UTC+9) 기준</span></h2>
      <div id="upcomingTable"></div>
    </div>
  </section>

  <section id="t-spec" class="tab">
    <div class="card">
      <h2>🚀 로켓 실루엣 비교 <span class="muted" style="font-size:12px">— 실제 높이 비율, 호버 시 상세</span><span class="dq-badge dq-B" data-dq="B" data-dq-source="Launch Library 2 + user-curated 사양"><span class="dq-dot"></span>Grade B · 검증됨</span></h2>
      <div class="rs-legend" aria-label="re-use legend">
        <span class="rs-legend-item"><span class="rs-legend-swatch rs-legend-solid"></span><b>솔리드</b> = 재사용 단</span>
        <span class="rs-legend-item"><span class="rs-legend-swatch rs-legend-stripe"></span><b>줄무늬</b> = 소모 단 (1회용)</span>
        <span class="rs-legend-item muted">하단 dot = 단별 재사용 여부 (좌→1단)</span>
      </div>
      <div class="rs-stage" id="rsStage" aria-label="rocket silhouette stage"></div>
      <div class="rs-foot-row" id="rsFootRow" aria-label="rocket silhouette labels"></div>
    </div>

    <div class="card">
      <h2>📊 LEO 적재량 × $/kg 버블 차트 <span class="muted" style="font-size:12px">— 버블 크기 = 누적 발사 횟수</span></h2>
      <div class="bc-wrap"><canvas id="bcCanvas" class="bc-canvas"></canvas></div>
      <div class="bc-hidden-list" id="bcHiddenList" aria-label="cost-undisclosed rockets"></div>
    </div>

    <div class="card">
      <h2>로켓·미션 비교 테이블 <span class="muted" style="font-size:12px">— 컬럼 헤더 클릭하면 정렬</span></h2>
      <p class="muted" id="curatedSummary" style="margin-bottom:8px">로딩…</p>
      <div id="curatedTable"></div>
    </div>

    <div class="card">
      <h2>🤖 AI 브리핑</h2>
      <p class="muted" style="margin-bottom:12px">표에서 행을 클릭하면 해당 항목 카드가 펼쳐집니다.</p>
      <div id="curatedBriefings"></div>
    </div>
  </section>

  <section id="t-hist" class="tab">
    <div class="grid cols-3">
      <div class="card" style="height:280px"><canvas id="donutSpaceX" style="width:100%;height:100%"></canvas></div>
      <div class="card" style="height:280px"><canvas id="donutRocketLab" style="width:100%;height:100%"></canvas></div>
      <div class="card" style="height:280px"><canvas id="donutBlueOrigin" style="width:100%;height:100%"></canvas></div>
    </div>
    <div class="card">
      <h2>발사 이력 (UTC) <span class="muted" style="font-size:12px">— 필터/정렬 가능 · 시각은 UTC 그대로</span><span class="dq-badge dq-B" data-dq="B" data-dq-source="Launch Library 2 (독립 검증 플랫폼)"><span class="dq-dot"></span>Grade B · 검증됨</span></h2>
      <div id="historyTable"></div>
    </div>
  </section>

  <section id="t-stock" class="tab">
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;font-size:14px;color:var(--muted)">
      📈 <b style="color:var(--text);font-weight:500">인사이트</b> <span class="muted">— 분석·집계 기반 통계</span>
      <span class="dq-badge dq-C" data-dq="C" data-dq-source="LL2 데이터 + TSR 자체 분석·집계 (1.5차 + 2차 혼합)" style="margin-left:auto"><span class="dq-dot"></span>Grade C · 분석 포함</span>
    </div>
    <div class="grid cols-2">
      <div class="card" style="height:420px"><canvas id="yoyBar" style="width:100%;height:100%"></canvas></div>
      <div class="card" style="height:420px"><canvas id="successLine" style="width:100%;height:100%"></canvas></div>
    </div>
    <div class="grid cols-2">
      <div class="card" style="height:420px"><canvas id="axisPie" style="width:100%;height:100%"></canvas></div>
      <div class="card" id="axisInsightCard">
        <h2>🤖 AI 해석 — 4축 분포가 말하는 것</h2>
        <div id="axisInsight"><p class="muted">분석 중…</p></div>
      </div>
    </div>
    <div id="mi-section">
      <div class="card">
        <h2>🎯 주목 상업 미션 <span class="muted" style="font-size:12px">— 섹터 탭으로 필터, 카드 클릭 시 타임라인에서 강조</span></h2>
        <div class="mi-tabs" id="miTabs" role="tablist"></div>
        <div class="mi-grid" id="miGrid"></div>
      </div>
      <div class="card">
        <h2>🗓️ 미션 타임라인 (2020 ~ 2030)</h2>
        <div class="mi-timeline-wrap"><svg id="miTimeline" class="mi-timeline" aria-label="mission timeline"></svg></div>
      </div>
    </div>
  </section>


</main>

<footer class="dq-footer" id="dqFooter">
  <div class="dq-disclaimer">
    <b>⚠️ AI 분석 기반 정보입니다</b> — 부정확하거나 지연된 데이터가 포함될 수 있습니다.
    투자 판단 등 중요 결정에 단독 활용을 권장하지 않습니다.
  </div>
  <div class="dq-footer-meta">
    Last update: <b id="dqFooterUpdate">—</b>
  </div>
</footer>

<script>
// ── Async data bootstrap: fetch 로컬 JSON (localhost http server 전용) ──
// inline payload 는 제거됨. file:// 직접 열기 미지원.
let D = null;
const $ = (id) => document.getElementById(id);
let COMPANY_COLOR, DEFAULT_COLOR, AXIS_LABEL, AXIS_COLOR;

// 데이터 로드 후 호출될 init 함수 큐 (각 IIFE 가 등록)
const _initQueue = [];
function tsrInit(fn) {{ _initQueue.push(fn); }}

async function _loadData() {{
  const resp = await fetch('rocket_dashboard_data.json', {{cache:'no-cache'}});
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const data = await resp.json();
  console.info('[TSR] data loaded via fetch:', data.now);
  return data;
}}

function colorFor(company) {{
  if (!company) return DEFAULT_COLOR;
  for (const k of Object.keys(COMPANY_COLOR || {{}})) {{
    if (company.toLowerCase().includes(k.toLowerCase())) return COMPANY_COLOR[k];
  }}
  return DEFAULT_COLOR;
}}

// ─────────────────────────────────────────────────────────────────
// 🎨 Canvas Viz helpers (cv- prefix) — Plotly 대체 vanilla 차트
// ─────────────────────────────────────────────────────────────────
const CV = (function() {{
  // 공유 tooltip (body 직속, 한 번만 생성)
  let tip = null;
  function getTip() {{
    if (!tip) {{
      tip = document.getElementById('cv-tooltip');
      if (!tip) {{
        tip = document.createElement('div');
        tip.id = 'cv-tooltip';
        tip.className = 'cv-tooltip';
        document.body.appendChild(tip);
      }}
    }}
    return tip;
  }}
  function showTip(html, x, y) {{
    const t = getTip();
    t.innerHTML = html;
    t.classList.add('cv-visible');
    moveTip(x, y);
  }}
  function moveTip(cx, cy) {{
    const t = getTip();
    const m = 14, tw = t.offsetWidth, th = t.offsetHeight;
    let x = cx + m, y = cy + m;
    if (x + tw > window.innerWidth - 8) x = cx - tw - m;
    if (y + th > window.innerHeight - 8) y = cy - th - m;
    if (x < 8) x = 8; if (y < 8) y = 8;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }}
  function hideTip() {{ if (tip) tip.classList.remove('cv-visible'); }}

  function dprResize(canvas, h) {{
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return {{ctx, w, h}};
  }}

  function makeResponsive(canvas, redraw) {{
    canvas._cvRedraw = redraw;
    canvas.dataset.cv = '1';
    if (typeof ResizeObserver !== 'undefined') {{
      new ResizeObserver(() => redraw()).observe(canvas.parentElement);
    }}
    window.addEventListener('resize', redraw);
  }}

  // ── 1) StackedBar — 누적 세로 막대 ─────────────────────────
  function StackedBar(canvas, opts) {{
    // opts: {{categories:[], series:[{{name, color, values:[]}}], yLabel}}
    const PAD = {{top:24, right:16, bottom:42, left:50}};
    let hoverIdx = null;

    function redraw() {{
      const {{ctx, w, h}} = dprResize(canvas, canvas.parentElement.clientHeight || 380);
      ctx.clearRect(0, 0, w, h);
      const cats = opts.categories;
      const series = opts.series;
      // y max = max stack
      const stackTotals = cats.map((_, i) =>
        series.reduce((s, ser) => s + (ser.values[i] || 0), 0));
      const yMax = Math.max(1, Math.ceil(Math.max(...stackTotals) / 10) * 10);
      const innerW = w - PAD.left - PAD.right;
      const innerH = h - PAD.top - PAD.bottom;
      const barW = Math.max(2, Math.min(36, innerW / cats.length * 0.7));
      const xStep = innerW / cats.length;

      // y grid
      ctx.font = '11px -apple-system, "Pretendard", sans-serif';
      ctx.textBaseline = 'middle';
      ctx.strokeStyle = 'rgba(139,148,158,0.18)';
      ctx.fillStyle = '#8b949e';
      ctx.lineWidth = 1;
      const yTicks = 5;
      for (let i = 0; i <= yTicks; i++) {{
        const v = yMax * i / yTicks;
        const y = PAD.top + innerH - (v / yMax) * innerH;
        ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
        ctx.textAlign = 'right';
        ctx.fillText(Math.round(v), PAD.left - 8, y);
      }}
      // x labels (every Nth)
      ctx.fillStyle = '#8b949e';
      ctx.textBaseline = 'top';
      ctx.textAlign = 'center';
      const skip = Math.ceil(cats.length / 14);
      cats.forEach((c, i) => {{
        if (i % skip !== 0 && i !== cats.length - 1) return;
        const x = PAD.left + xStep * (i + 0.5);
        ctx.fillText(c, x, h - PAD.bottom + 6);
      }});

      // bars (stacked)
      const bars = [];
      cats.forEach((c, i) => {{
        let yBase = PAD.top + innerH;
        const x = PAD.left + xStep * (i + 0.5) - barW/2;
        const segments = [];
        series.forEach(ser => {{
          const v = ser.values[i] || 0;
          if (v <= 0) return;
          const segH = (v / yMax) * innerH;
          ctx.fillStyle = ser.color;
          ctx.fillRect(x, yBase - segH, barW, segH);
          segments.push({{name:ser.name, value:v, color:ser.color}});
          yBase -= segH;
        }});
        // hover highlight
        if (i === hoverIdx) {{
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1.5;
          ctx.strokeRect(x - 1, PAD.top, barW + 2, innerH);
        }}
        bars.push({{x, y: PAD.top, w: barW, h: innerH, idx: i, cat: c, segments,
          total: stackTotals[i]}});
      }});

      // axis title
      ctx.fillStyle = '#8b949e';
      ctx.font = '12px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(opts.yLabel || '', PAD.left + innerW/2, h - 6);
      // legend
      let legX = PAD.left;
      const legY = 12;
      ctx.font = '11px -apple-system, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      series.forEach(ser => {{
        ctx.fillStyle = ser.color;
        ctx.fillRect(legX, legY - 5, 10, 10);
        ctx.fillStyle = '#e6edf3';
        ctx.fillText(ser.name, legX + 14, legY);
        legX += ctx.measureText(ser.name).width + 36;
      }});
      canvas._cvBars = bars;
    }}

    canvas.addEventListener('mousemove', (e) => {{
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const bars = canvas._cvBars || [];
      let found = null;
      const xStep = bars.length ? (bars[1] ? bars[1].x - bars[0].x : 30) : 30;
      bars.forEach(b => {{
        if (x >= b.x - xStep/2 + b.w/2 && x <= b.x + xStep/2 + b.w/2) found = b;
      }});
      if (found && hoverIdx !== found.idx) {{ hoverIdx = found.idx; redraw(); }}
      if (!found && hoverIdx !== null) {{ hoverIdx = null; redraw(); }}
      if (found) {{
        const rows = found.segments.slice().reverse().map(s =>
          `<div class="cv-tooltip-row"><span style="color:${{s.color}}">●</span> ${{s.name}}<b>${{s.value}}</b></div>`).join('');
        showTip(`<div class="cv-tooltip-title">${{found.cat}}</div>${{rows}}<div class="cv-tooltip-row"><span>합계</span><b>${{found.total}}</b></div>`,
          e.clientX, e.clientY);
      }} else {{ hideTip(); }}
    }});
    canvas.addEventListener('mouseleave', () => {{
      if (hoverIdx !== null) {{ hoverIdx = null; redraw(); }}
      hideTip();
    }});
    makeResponsive(canvas, redraw);
    redraw();
  }}

  // ── 2) MultiLine — 다중 라인 차트 ───────────────────────────
  function MultiLine(canvas, opts) {{
    // opts: {{xs:[], series:[{{name, color, values:[]}}], yLabel, yMax, yMin}}
    const PAD = {{top:24, right:16, bottom:42, left:50}};
    let hover = null; // {{seriesIdx, pointIdx}}

    function redraw() {{
      const {{ctx, w, h}} = dprResize(canvas, canvas.parentElement.clientHeight || 380);
      ctx.clearRect(0, 0, w, h);
      const xs = opts.xs;
      const series = opts.series;
      const innerW = w - PAD.left - PAD.right;
      const innerH = h - PAD.top - PAD.bottom;
      const yMin = (opts.yMin != null) ? opts.yMin : 0;
      const yMax = (opts.yMax != null) ? opts.yMax : 100;
      const xOf = (i) => PAD.left + (xs.length === 1 ? innerW/2 : (i / (xs.length - 1)) * innerW);
      const yOf = (v) => PAD.top + innerH - ((v - yMin) / (yMax - yMin)) * innerH;

      ctx.font = '11px -apple-system, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.strokeStyle = 'rgba(139,148,158,0.18)';
      ctx.fillStyle = '#8b949e';
      ctx.lineWidth = 1;
      // y grid
      const yTicks = 5;
      for (let i = 0; i <= yTicks; i++) {{
        const v = yMin + (yMax - yMin) * i / yTicks;
        const y = yOf(v);
        ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
        ctx.textAlign = 'right';
        ctx.fillText(Math.round(v) + '%', PAD.left - 8, y);
      }}
      // x labels
      ctx.fillStyle = '#8b949e';
      ctx.textBaseline = 'top';
      ctx.textAlign = 'center';
      const skip = Math.ceil(xs.length / 12);
      xs.forEach((x, i) => {{
        if (i % skip !== 0 && i !== xs.length - 1) return;
        ctx.fillText(x, xOf(i), h - PAD.bottom + 6);
      }});

      // series lines
      const points = [];
      series.forEach((ser, sIdx) => {{
        ctx.strokeStyle = ser.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ser.values.forEach((v, i) => {{
          if (v == null) return;
          const x = xOf(i), y = yOf(v);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          points.push({{x, y, sIdx, pIdx:i, name:ser.name, color:ser.color, value:v, label:xs[i]}});
        }});
        ctx.stroke();
        // markers
        ser.values.forEach((v, i) => {{
          if (v == null) return;
          const x = xOf(i), y = yOf(v);
          const r = (hover && hover.sIdx === sIdx && hover.pIdx === i) ? 5 : 3;
          ctx.fillStyle = ser.color;
          ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI*2); ctx.fill();
          ctx.strokeStyle = '#161b22';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }});
      }});

      // legend
      let legX = PAD.left;
      const legY = 12;
      ctx.font = '11px -apple-system, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      series.forEach(ser => {{
        ctx.fillStyle = ser.color;
        ctx.fillRect(legX, legY - 5, 10, 10);
        ctx.fillStyle = '#e6edf3';
        ctx.fillText(ser.name, legX + 14, legY);
        legX += ctx.measureText(ser.name).width + 36;
      }});
      canvas._cvPoints = points;
    }}

    canvas.addEventListener('mousemove', (e) => {{
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      const points = canvas._cvPoints || [];
      let best = null, bestD = 16;
      points.forEach(p => {{
        const dx = x - p.x, dy = y - p.y;
        const d = Math.sqrt(dx*dx + dy*dy);
        if (d < bestD) {{ bestD = d; best = p; }}
      }});
      const newHover = best ? {{sIdx:best.sIdx, pIdx:best.pIdx}} : null;
      if (JSON.stringify(newHover) !== JSON.stringify(hover)) {{ hover = newHover; redraw(); }}
      if (best) {{
        showTip(`<div class="cv-tooltip-title">${{best.label}}</div>
          <div class="cv-tooltip-row"><span style="color:${{best.color}}">●</span> ${{best.name}}<b>${{best.value.toFixed(1)}}%</b></div>`,
          e.clientX, e.clientY);
      }} else {{ hideTip(); }}
    }});
    canvas.addEventListener('mouseleave', () => {{
      if (hover) {{ hover = null; redraw(); }} hideTip();
    }});
    makeResponsive(canvas, redraw);
    redraw();
  }}

  // ── 3) Donut — 도넛 차트 ────────────────────────────────────
  function Donut(canvas, opts) {{
    // opts: {{slices:[{{label, value, color}}], title, hole:0.55, centerLabel}}
    let hoverIdx = null;
    function redraw() {{
      const h0 = canvas.parentElement.clientHeight || 240;
      const {{ctx, w, h}} = dprResize(canvas, h0);
      ctx.clearRect(0, 0, w, h);
      const slices = opts.slices.filter(s => s.value > 0);
      const total = slices.reduce((s, x) => s + x.value, 0);
      if (total === 0) {{
        ctx.fillStyle = '#8b949e'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = '13px sans-serif';
        ctx.fillText('데이터 없음', w/2, h/2);
        return;
      }}
      // title
      ctx.fillStyle = '#e6edf3';
      ctx.font = 'bold 13px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(opts.title || '', w/2, 8);

      const cx = w/2, cy = h/2 + 8;
      const radius = Math.min(w, h - 30) / 2 - 36;
      const inner = radius * (opts.hole != null ? opts.hole : 0.55);
      let angle = -Math.PI/2;
      const segs = [];
      slices.forEach((s, i) => {{
        const sweep = (s.value / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
        ctx.arc(cx, cy, radius, angle, angle + sweep);
        ctx.arc(cx, cy, inner, angle + sweep, angle, true);
        ctx.closePath();
        ctx.fillStyle = s.color;
        ctx.fill();
        if (i === hoverIdx) {{
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 2;
          ctx.stroke();
        }}
        // label outside (percent)
        const mid = angle + sweep/2;
        const pct = (s.value/total*100);
        if (pct >= 4) {{
          const lx = cx + Math.cos(mid) * (radius + 14);
          const ly = cy + Math.sin(mid) * (radius + 14);
          ctx.fillStyle = '#e6edf3';
          ctx.font = '10px -apple-system, sans-serif';
          ctx.textAlign = mid > -Math.PI/2 && mid < Math.PI/2 ? 'left' : 'right';
          ctx.textBaseline = 'middle';
          ctx.fillText(`${{s.label}} ${{pct.toFixed(0)}}%`, lx, ly);
        }}
        segs.push({{startAngle:angle, endAngle:angle + sweep, idx:i, slice:s}});
        angle += sweep;
      }});
      // 중앙 라벨 (가장 큰 섹터)
      if (opts.centerLabel) {{
        const top = slices.slice().sort((a,b) => b.value - a.value)[0];
        ctx.fillStyle = '#e6edf3';
        ctx.font = 'bold 14px -apple-system, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(top.label, cx, cy - 8);
        ctx.font = '11px -apple-system, sans-serif';
        ctx.fillStyle = '#8b949e';
        ctx.fillText(`${{(top.value/total*100).toFixed(1)}}%`, cx, cy + 10);
      }}
      canvas._cvDonut = {{cx, cy, radius, inner, segs, total}};
    }}

    canvas.addEventListener('mousemove', (e) => {{
      const d = canvas._cvDonut;
      if (!d) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      const dx = x - d.cx, dy = y - d.cy;
      const dist = Math.sqrt(dx*dx + dy*dy);
      let found = null;
      if (dist >= d.inner && dist <= d.radius) {{
        let a = Math.atan2(dy, dx);
        if (a < -Math.PI/2) a += Math.PI * 2;
        d.segs.forEach(s => {{
          let sa = s.startAngle, ea = s.endAngle;
          if (sa < -Math.PI/2) sa += Math.PI * 2;
          if (ea < -Math.PI/2) ea += Math.PI * 2;
          if (a >= sa && a <= ea) found = s;
        }});
      }}
      const newIdx = found ? found.idx : null;
      if (newIdx !== hoverIdx) {{ hoverIdx = newIdx; redraw(); }}
      if (found) {{
        const s = found.slice;
        showTip(`<div class="cv-tooltip-title" style="color:${{s.color}}">${{s.label}}</div>
          <div class="cv-tooltip-row"><span>비율</span><b>${{(s.value/d.total*100).toFixed(1)}}%</b></div>
          <div class="cv-tooltip-row"><span>건수</span><b>${{s.value}}건</b></div>`,
          e.clientX, e.clientY);
      }} else {{ hideTip(); }}
    }});
    canvas.addEventListener('mouseleave', () => {{
      if (hoverIdx !== null) {{ hoverIdx = null; redraw(); }} hideTip();
    }});
    makeResponsive(canvas, redraw);
    redraw();
  }}

  return {{StackedBar, MultiLine, Donut, showTip, hideTip, moveTip}};
}})();

// ── Bootstrap: fetch 로 데이터 로드 → 큐의 모든 init 실행 ──
(async function() {{
  try {{
    D = await _loadData();
  }} catch (e) {{
    console.error('[TSR] fetch failed:', e);
    document.body.innerHTML = `
      <div style="padding:40px;font-family:-apple-system,'SF Pro Text','Pretendard',sans-serif;color:#ccc;background:#111;min-height:100vh">
        <h2 style="color:#ffa657">⚠️ 데이터 로드 실패</h2>
        <p style="color:#bbb;margin-top:12px">이 대시보드는 <b>로컬 HTTP 서버</b> 환경에서만 동작합니다 (file:// 직접 열기 미지원).</p>
        <p style="color:#bbb;margin-top:18px">터미널에서 아래 명령어로 서버를 시작해주세요:</p>
        <pre style="background:#222;padding:16px;border-radius:8px;color:#7ec8e3;font-size:13px;line-height:1.6;overflow-x:auto">cd exports && python3 -m http.server 8080</pre>
        <p style="color:#bbb;margin-top:16px">그 후 브라우저에서:</p>
        <pre style="background:#222;padding:16px;border-radius:8px;color:#7ec8e3;font-size:13px;line-height:1.6;overflow-x:auto">http://localhost:8080/rocket_dashboard.html</pre>
        <p style="color:#888;margin-top:24px;font-size:12px">에러 상세: <code style="color:#ff6b6b">${{e && e.message ? e.message : e}}</code></p>
        <p style="color:#888;margin-top:8px;font-size:12px">데이터 갱신: <code>bash scripts/run_rocket_research.sh</code> (매일 09:00 KST 자동 실행됨)</p>
      </div>`;
    return;
  }}
  COMPANY_COLOR = D.company_colors;
  DEFAULT_COLOR = D.default_color;
  AXIS_LABEL = D.axis_label;
  AXIS_COLOR = D.axis_color;
  if ($('lastUpdate')) $('lastUpdate').textContent = D.now;
  if ($('dqFooterUpdate')) $('dqFooterUpdate').textContent = D.now;
  // 무결성 경고: 데이터 stale 시 푸터 메시지에 노출
  if (D.integrity && D.integrity.data_stale) {{
    const f = document.querySelector('.dq-disclaimer');
    if (f) {{
      f.innerHTML = `<b style="color:#ff6b6b">⚠️ 데이터 ${{D.integrity.stale_age_hours}}h 전 (24h 초과 stale)</b> — ` + f.innerHTML;
    }}
  }}
  for (const fn of _initQueue) {{
    try {{ fn(); }} catch (e) {{ console.error('[TSR] init failed:', e); }}
  }}
}})();

// ── Data Quality 뱃지 hover (이벤트 위임, 단일 tooltip) ─────────────
(function() {{
  const GRADE_DESC = {{
    A: {{title:'Grade A — 1차 단독', desc:'당사자 직접 발행 (SpaceX/RKLB/BO 공식)'}},
    B: {{title:'Grade B — 1차/1.5차 검증',  desc:'독립 검증 플랫폼 (Launch Library 2 등)'}},
    C: {{title:'Grade C — 분석·집계 포함', desc:'1.5차 + 2차 혼합, TSR 자체 분석'}},
    D: {{title:'Grade D — 미확인', desc:'2차 이하 또는 출처 미확인'}},
  }};
  let tip = null;
  function getTip() {{
    if (!tip) {{
      tip = document.createElement('div');
      tip.className = 'dq-tooltip';
      document.body.appendChild(tip);
    }}
    return tip;
  }}
  function moveTip(cx, cy) {{
    const t = getTip(), m = 12, tw = t.offsetWidth, th = t.offsetHeight;
    let x = cx + m, y = cy + m;
    if (x + tw > window.innerWidth - 8) x = cx - tw - m;
    if (y + th > window.innerHeight - 8) y = cy - th - m;
    if (x < 8) x = 8; if (y < 8) y = 8;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }}
  document.body.addEventListener('mouseover', (e) => {{
    const b = e.target.closest && e.target.closest('.dq-badge');
    if (!b) return;
    const grade = b.dataset.dq;
    const source = b.dataset.dqSource || '';
    const meta = GRADE_DESC[grade] || {{title:'Grade '+grade, desc:''}};
    const t = getTip();
    t.innerHTML = `<div class="dq-tooltip-title">${{meta.title}}</div>
      <div>${{meta.desc}}</div>
      ${{source ? `<div class="dq-tooltip-source" style="margin-top:4px">📦 ${{source}}</div>` : ''}}`;
    t.classList.add('dq-visible');
    moveTip(e.clientX, e.clientY);
  }});
  document.body.addEventListener('mousemove', (e) => {{
    if (!tip || !tip.classList.contains('dq-visible')) return;
    if (e.target.closest && e.target.closest('.dq-badge')) moveTip(e.clientX, e.clientY);
    else tip.classList.remove('dq-visible');
  }});
  document.body.addEventListener('mouseout', (e) => {{
    if (!tip) return;
    const next = e.relatedTarget;
    if (next && next instanceof Element && next.closest && next.closest('.dq-badge')) return;
    if (!e.target.closest || !e.target.closest('.dq-badge')) return;
    tip.classList.remove('dq-visible');
  }});
}})();

// ── tabs ───────────────────────────────────────────────
document.getElementById('tabs').addEventListener('click', (e) => {{
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  e.target.classList.add('active');
  const id = e.target.dataset.tab;
  const section = document.getElementById(id);
  section.classList.add('active');
  // 탭 활성화 후 캔버스 차트들이 새 크기로 재계산되도록 ResizeObserver 트리거
  window.dispatchEvent(new Event('resize'));
  // 즉시 한 번 더 redraw 트리거 (ResizeObserver 가 변화를 감지 못 한 경우 대비)
  section.querySelectorAll('canvas[data-cv]').forEach(c => {{
    if (c._cvRedraw) c._cvRedraw();
  }});
}});

// ── Countdown — status 기반 3단계 분기 + 자동 롤오버 + 호버 툴팁 ───
tsrInit(function () {{
  const ups = D.upcoming_90 || [];
  if (!ups.length) {{ $('countdown').textContent = '예정 발사 데이터 없음'; return; }}

  // 완료 상태로 간주할 abbrev (롤오버 시 건너뜀)
  const TERMINAL = new Set(['Success', 'Failure', 'Partial Failure']);

  function pickNext() {{
    const found = ups.find(r => {{
      if (!r.net) return false;
      const ab = (r.status || {{}}).abbrev || '';
      if (TERMINAL.has(ab)) return false;        // 종료된 미션 건너뜀
      return true;
    }});
    return found || ups.find(r => r.net) || ups[0];
  }}

  // 공유 tooltip (cv-tooltip 재사용)
  let cdTip = document.getElementById('cv-tooltip');
  if (!cdTip) {{
    cdTip = document.createElement('div');
    cdTip.id = 'cv-tooltip';
    cdTip.className = 'cv-tooltip';
    document.body.appendChild(cdTip);
  }}
  function fmtKST(iso) {{
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    // KST = UTC+9
    const k = new Date(d.getTime() + 9*3600*1000);
    const pad = (n) => String(n).padStart(2,'0');
    return `${{k.getUTCFullYear()}}-${{pad(k.getUTCMonth()+1)}}-${{pad(k.getUTCDate())}} ${{pad(k.getUTCHours())}}:${{pad(k.getUTCMinutes())}} KST`;
  }}
  function fmtUTC(iso) {{
    if (!iso) return '—';
    return iso.replace('T',' ').slice(0,16) + ' UTC';
  }}

  const cdEl = $('countdown');
  cdEl.style.cursor = 'help';
  let currentNext = null;

  function render() {{
    currentNext = pickNext();
    if (!currentNext || !currentNext.net) {{
      cdEl.innerHTML = '예정 발사 데이터 없음';
      return;
    }}
    const ms = new Date(currentNext.net).getTime() - Date.now();
    const ab = (currentNext.status || {{}}).abbrev || '';
    const sname = (currentNext.status || {{}}).name || '';
    const mname = currentNext.mission_name || currentNext.name || '';
    const company = currentNext.company || '';
    const rocket = currentNext.rocket_model || '';

    if (ms <= 0) {{
      // 분기 ① net 이 지났고 아직 진행 중일 가능성 (Go/TBC/TBD)
      if (ab === 'Go' || ab === 'TBC' || ab === 'TBD' || /in flight|launch in progress|hold/i.test(sname)) {{
        cdEl.innerHTML = `<span style="background:#D32F2F;color:#fff;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:5px"><span class="live-dot"></span>LIVE</span> &nbsp; ${{escapeAttr(mname)}}`;
        return;
      }}
      // 분기 ② 종료된 결과 표기
      if (ab === 'Success' || /successful/i.test(sname)) {{
        cdEl.innerHTML = `✅ 완료 · <b>${{escapeAttr(mname)}}</b>`;
        return;
      }}
      if (ab === 'Failure' || /failure/i.test(sname)) {{
        cdEl.innerHTML = `❌ 실패 · <b>${{escapeAttr(mname)}}</b>`;
        return;
      }}
      if (ab === 'Partial Failure' || /partial/i.test(sname)) {{
        cdEl.innerHTML = `⚠️ 부분성공 · <b>${{escapeAttr(mname)}}</b>`;
        return;
      }}
      // 분기 ④ status 미확정 fallback
      cdEl.innerHTML = `🚀 진행/완료: ${{escapeAttr(mname)}}`;
      return;
    }}

    // 분기 ③ 카운트다운
    const d = Math.floor(ms/86400000);
    const h = Math.floor((ms%86400000)/3600000);
    const m = Math.floor((ms%3600000)/60000);
    if (d === 0) {{
      cdEl.innerHTML = `🕐 <b>D-0 · 오늘 발사</b> · ${{escapeAttr(company)}} · ${{escapeAttr(rocket)}} · ${{escapeAttr(mname)}} (${{h}}h ${{m}}m)`;
    }} else {{
      cdEl.innerHTML = `🕐 다음 발사 <b>D-${{d}}일</b> · ${{escapeAttr(company)}} · ${{escapeAttr(rocket)}} · ${{escapeAttr(mname)}} (${{h}}h ${{m}}m)`;
    }}
  }}
  function escapeAttr(s) {{
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
  }}

  // 호버 툴팁
  cdEl.addEventListener('mouseenter', (e) => {{
    if (!currentNext) return;
    const r = currentNext;
    const html = `
      <div class="cv-tooltip-title">${{escapeAttr(r.mission_name || r.name || '—')}}</div>
      <div class="cv-tooltip-row"><span>로켓</span><b>${{escapeAttr(r.rocket_model || '—')}}</b></div>
      <div class="cv-tooltip-row"><span>발사 기관</span><b>${{escapeAttr(r.company || '—')}}</b></div>
      <div class="cv-tooltip-row"><span>UTC</span><b>${{escapeAttr(fmtUTC(r.net))}}</b></div>
      <div class="cv-tooltip-row"><span>KST</span><b>${{escapeAttr(fmtKST(r.net))}}</b></div>
      <div class="cv-tooltip-row"><span>상태</span><b>${{escapeAttr((r.status||{{}}).name || (r.status||{{}}).abbrev || '—')}}</b></div>`;
    cdTip.innerHTML = html;
    cdTip.classList.add('cv-visible');
  }});
  cdEl.addEventListener('mousemove', (e) => {{
    if (!cdTip.classList.contains('cv-visible')) return;
    const m = 14, tw = cdTip.offsetWidth, th = cdTip.offsetHeight;
    let x = e.clientX + m, y = e.clientY + m;
    if (x + tw > window.innerWidth - 8) x = e.clientX - tw - m;
    if (y + th > window.innerHeight - 8) y = e.clientY - th - m;
    if (x < 8) x = 8; if (y < 8) y = 8;
    cdTip.style.left = x + 'px'; cdTip.style.top = y + 'px';
  }});
  cdEl.addEventListener('mouseleave', () => {{
    cdTip.classList.remove('cv-visible');
  }});

  render();
  // 매 60초마다 next 재선택 + 표시 갱신 (자동 롤오버)
  setInterval(render, 60000);
}});

// ── Tab 1: Vanilla JS Density Heatmap (replaces Plotly gantt) ────
tsrInit(function () {{
  // 1) confirmed/day-precision (90일 윈도우) + 2) 모든 미정 발사 (best-guess 매핑)
  const upsRaw = [...(D.upcoming_90 || []), ...(D.upcoming_indeterminate || [])];
  const _seen = new Set();
  const ups = [];
  for (const r of upsRaw) {{
    const k = r.id || (r.net + '||' + (r.mission_name||r.name||''));
    if (_seen.has(k)) continue;
    _seen.add(k);
    ups.push(r);
  }}
  const root = document.getElementById('hmCalendar');
  if (!root) return;
  if (!ups.length) {{
    root.innerHTML = '<p class="muted" style="padding:20px">예정 발사 데이터 없음</p>';
  }} else {{

  // ── Helpers ──────────────────────────────────────
  function mondayOf(dStr) {{
    const dt = new Date(dStr);
    const dow = (dt.getUTCDay() + 6) % 7;            // Mon=0..Sun=6
    dt.setUTCDate(dt.getUTCDate() - dow);
    dt.setUTCHours(0, 0, 0, 0);
    return dt;
  }}
  function weekKey(monday) {{ return monday.toISOString().slice(0, 10); }}
  function weekLabel(monday) {{
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dom = monday.getUTCDate();
    return `${{months[monday.getUTCMonth()]}} W${{Math.ceil(dom/7)}}`;
  }}
  function shortAgency(name) {{
    if (!name) return 'Unknown';
    const map = {{
      'spacex': 'SpaceX',
      'rocket lab ltd': 'Rocket Lab',
      'rocket lab': 'Rocket Lab',
      'blue origin': 'Blue Origin',
      'united launch alliance': 'ULA',
      'mitsubishi heavy industries': 'MHI',
      'arianespace': 'Arianespace',
      'roscosmos': 'Roscosmos',
      'russian federal space agency': 'Roscosmos',
      'rkk energiya': 'RKK Energiya',
      'china aerospace science and technology corporation': 'CASC',
      'china aerospace': 'CASC',
      'casc': 'CASC',
      'cnsa': 'CNSA',
      'galactic energy': 'Galactic Energy',
      'landspace': 'LandSpace',
      'cas space': 'CAS Space',
      'orienspace': 'Orienspace',
      'space pioneer': 'Space Pioneer',
      'indian space research': 'ISRO',
      'isro': 'ISRO',
      'jaxa': 'JAXA',
      'iranian space agency': 'ISA',
      'firefly aerospace': 'Firefly',
      'astra': 'Astra',
      'stoke space': 'Stoke',
      'isar aerospace': 'Isar',
      'add ': 'ADD',
      'agencia espacial brasileira': 'AEB',
      'rocket factory augsburg': 'RFA',
      'innospace': 'Innospace',
      'gilmour space': 'Gilmour',
      'ula ': 'ULA'
    }};
    const lower = name.toLowerCase();
    for (const k of Object.keys(map)) {{
      if (lower.includes(k)) return map[k];
    }}
    return name.length > 22 ? name.slice(0, 20) + '…' : name;
  }}
  function statusOf(rec) {{
    const s = rec.status || {{}};
    const abbrev = String(s.abbrev || '').toLowerCase();
    const sname = String(s.name || '').toLowerCase();
    if (abbrev === 'go' || sname.includes('go for launch') || sname.includes('successful')) return 'confirmed';
    if (abbrev === 'tbc' || sname.includes('to be confirmed')) return 'tentative';
    if (abbrev === 'tbd' || sname.includes('to be determined') || sname.includes('hold')) return 'tbd';
    return 'tbd';
  }}
  function escapeHtml(s) {{
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]
    );
  }}
  function fmtDate(net) {{
    if (!net) return '—';
    return net.replace('T', ' ').slice(0, 16) + ' UTC';
  }}

  // 미정 일정 best-guess 매핑 (월=15일 / 분기=중간달 15일 / 연=7-15)
  function bestGuessISO(rec) {{
    const p = rec.net_precision || 'confirmed';
    if (p === 'confirmed' || p === 'day') return rec.net;
    if (!rec.net || rec.net.length < 10) return rec.net;
    const yyyy = rec.net.slice(0, 4);
    const mm = rec.net.slice(5, 7);
    if (p === 'month') return `${{yyyy}}-${{mm}}-15T12:00:00Z`;
    if (p === 'quarter') {{
      const mid = {{'03':'02','06':'05','09':'08','12':'11'}}[mm] || '06';
      return `${{yyyy}}-${{mid}}-15T12:00:00Z`;
    }}
    if (p === 'year') return `${{yyyy}}-07-15T12:00:00Z`;
    return rec.net;
  }}
  function isUncertain(rec) {{
    const p = rec.net_precision;
    return p === 'month' || p === 'quarter' || p === 'year' || p === 'unknown';
  }}

  // ── 1) 표시 대상 기업 10개 (고정 순서) — 그 외는 하단 표에서 확인 ──
  const MAJOR_AGENCIES = [
    'SpaceX', 'Blue Origin', 'Rocket Lab', 'Astra',
    'ULA', 'Arianespace', 'MHI', 'ISRO', 'CASC', 'Firefly'
  ];
  const MAJOR_SET = new Set(MAJOR_AGENCIES);
  const AGENCY_FLAG = {{
    'SpaceX': '🇺🇸',
    'Blue Origin': '🇺🇸',
    'Rocket Lab': '🇺🇸',          // RKLB (US-listed, Long Beach HQ + Mahia NZ)
    'Astra': '🇺🇸',
    'ULA': '🇺🇸',
    'Arianespace': '🇪🇺',         // 다국적 (FR HQ, Ariane 6)
    'MHI': '🇯🇵',
    'ISRO': '🇮🇳',
    'CASC': '🇨🇳',
    'Firefly': '🇺🇸',
  }};

  // ── 2) Group launches by (agency, weekKey) — 미정 포함, best-guess 일자로 매핑 ──
  const cells = new Map();          // key: "agency||weekKey" → launches[]
  const weekMeta = new Map();       // weekKey → {{monday, label}}
  // 표시 윈도우: 어제부터 +13개월 (year-precision Q4 dump 까지 포섭)
  const winStart = mondayOf(new Date(Date.now() - 1*86400000).toISOString());
  const winEnd = new Date(Date.now() + 395*86400000);
  for (const r of ups) {{
    const ag = shortAgency(r.company);
    if (!MAJOR_SET.has(ag)) continue;        // 10개 외 기업은 히트맵 제외
    const eff = bestGuessISO(r);
    if (!eff) continue;
    const monday = mondayOf(eff);
    if (monday < winStart || monday > winEnd) continue;
    const wk = weekKey(monday);
    if (!weekMeta.has(wk)) weekMeta.set(wk, {{monday, label: weekLabel(monday)}});
    const k = ag + '||' + wk;
    if (!cells.has(k)) cells.set(k, []);
    cells.get(k).push({{...r, _eff: eff, _uncertain: isUncertain(r)}});
  }}
  // (※ "해당 달력만 표기" — 빈 주차는 채우지 않음. 발사가 있는 주차만 컬럼으로 노출.)

  // ── 3) 행 순서 = MAJOR_AGENCIES 고정 순서 (0건 기관도 행 표시 → 추적 중임을 명시) ──
  const sortedAgencies = MAJOR_AGENCIES;

  // ── 3) Sort weeks chronologically ────────────────
  const sortedWeeks = [...weekMeta.entries()].sort((a, b) => a[1].monday - b[1].monday);

  // ── 4) Build the grid (single DOM tree) ──────────
  const grid = document.createElement('div');
  grid.className = 'hm-grid';
  // 풀 폭 stretch — 라벨 컬럼은 고정(≤110px), 주차 컬럼은 1fr 으로 화면을 균등 분할
  grid.style.gridTemplateColumns = `minmax(80px, 110px) repeat(${{sortedWeeks.length}}, minmax(0, 1fr))`;

  // header row: corner + week labels
  const corner = document.createElement('div');
  corner.className = 'hm-corner';
  grid.appendChild(corner);
  for (const [wk, w] of sortedWeeks) {{
    const h = document.createElement('div');
    h.className = 'hm-col-header';
    h.textContent = w.label;
    grid.appendChild(h);
  }}

  // data rows
  for (const ag of sortedAgencies) {{
    const lbl = document.createElement('div');
    lbl.className = 'hm-row-label';
    const flag = AGENCY_FLAG[ag] || '';
    lbl.textContent = (flag ? flag + ' ' : '') + ag;
    grid.appendChild(lbl);
    for (const [wk] of sortedWeeks) {{
      const arr = cells.get(ag + '||' + wk) || [];
      const n = arr.length;
      const nUncertain = arr.filter(r => r._uncertain).length;
      const cell = document.createElement('div');
      let lvl;
      if (n === 0) lvl = 'zero';
      else if (nUncertain === n) lvl = 'uncertain-only'; // 전부 미정
      else lvl = (n === 1 ? '1' : (n === 2 ? '2' : '3'));
      cell.className = 'hm-cell hm-' + lvl;
      // confirmed + uncertain 혼합 시 줄무늬 오버레이만 추가 (배경색은 confirmed 기준)
      if (nUncertain > 0 && nUncertain < n) cell.classList.add('hm-uncertain');
      cell.textContent = n === 0 ? '' : String(n);
      cell.dataset.agency = ag;
      cell.dataset.week = wk;
      cell.dataset.weekLabel = (weekMeta.get(wk) || {{label: ''}}).label;
      cell.dataset.count = String(n);
      cell.dataset.uncertainCount = String(nUncertain);
      grid.appendChild(cell);
    }}
  }}
  root.innerHTML = '';
  root.appendChild(grid);

  // ── 5) Tooltip — single body-level element, reused ─
  let tip = document.getElementById('hmTooltip');
  if (!tip) {{
    tip = document.createElement('div');
    tip.id = 'hmTooltip';
    tip.className = 'hm-tooltip';
    document.body.appendChild(tip);
  }}

  function renderTip(ag, wkLabel, launches) {{
    const items = launches.map(r => {{
      const st = statusOf(r);
      const stLabel = st === 'confirmed' ? '확정' : (st === 'tentative' ? '잠정' : '미정');
      const uncertainBanner = r._uncertain
        ? `<div style="background:#3a2c14;color:#ffd479;padding:4px 6px;border-radius:4px;margin:6px 0;font-size:11px">⚠ ${{escapeHtml(r.net_label||'미정')}} — best-guess 매핑, <b>일정 재확인 필요</b></div>`
        : '';
      const dateLine = r._uncertain
        ? `<div class="hm-launch-row"><span class="hm-launch-label">발사일</span><span class="hm-launch-value">${{escapeHtml(r.net_label||'—')}}</span></div>`
        : `<div class="hm-launch-row"><span class="hm-launch-label">발사일</span><span class="hm-launch-value">${{escapeHtml(fmtDate(r.net))}}</span></div>`;
      return `
        <div class="hm-launch-item">
          ${{uncertainBanner}}
          <div class="hm-launch-row"><span class="hm-launch-label">로켓</span><span class="hm-launch-value">${{escapeHtml(r.rocket_model || '—')}}</span></div>
          <div class="hm-launch-row"><span class="hm-launch-label">미션</span><span class="hm-launch-value">${{escapeHtml(r.mission_name || r.name || '—')}}</span></div>
          <div class="hm-launch-row"><span class="hm-launch-label">고객</span><span class="hm-launch-value">${{escapeHtml(r.customer || r.company || '—')}}</span></div>
          ${{dateLine}}
          <div class="hm-launch-row"><span class="hm-launch-label">상태</span><span class="hm-status-pill hm-status-${{st}}">${{stLabel}}</span></div>
        </div>`;
    }}).join('');
    const nUncertain = launches.filter(r => r._uncertain).length;
    const headerExtra = nUncertain > 0
      ? ` <span style="color:#ffd479;font-size:10px">(⚠ ${{nUncertain}}건 미정)</span>`
      : '';
    const overflowHint = launches.length >= 8
      ? `<div class="hm-tooltip-scrollcue">↓ 스크롤 또는 📌 핀</div>`
      : '';
    const flag = AGENCY_FLAG[ag] || '';
    return `
      <div class="hm-tooltip-header">
        <span>${{flag ? flag + ' ' : ''}}${{escapeHtml(ag)}} · ${{escapeHtml(wkLabel)}} · ${{launches.length}}회 발사${{headerExtra}}</span>
        <span class="hm-tooltip-pin" id="hmTipPin" title="고정 (클릭/Esc 해제)">📌 핀</span>
      </div>
      <div class="hm-tooltip-body">${{items}}</div>
      ${{overflowHint}}`;
  }}

  // ── 6) Event delegation on grid ──────────────────
  let activeCell = null;
  let pinned = false;
  let hideTimer = null;
  function clearHideTimer() {{ if (hideTimer) {{ clearTimeout(hideTimer); hideTimer = null; }} }}
  function hideTip(force) {{
    if (pinned && !force) return;
    clearHideTimer();
    activeCell = null;
    tip.classList.remove('hm-visible');
  }}
  function scheduleHide(ms) {{
    clearHideTimer();
    hideTimer = setTimeout(() => hideTip(false), ms);
  }}
  // 셀 기준으로 tooltip 앵커 (커서 추적 X — 안정적 스크롤 가능)
  function positionTip(cell) {{
    // 먼저 보이게 한 뒤 크기 계산
    tip.style.left = '0px';
    tip.style.top = '0px';
    const rect = cell.getBoundingClientRect();
    const tw = Math.min(tip.offsetWidth || 320, 420);
    const th = Math.min(tip.offsetHeight || 200, Math.floor(window.innerHeight * 0.7));
    const margin = 8;
    // 우측 공간 우선, 없으면 좌측
    let x = rect.right + margin;
    if (x + tw > window.innerWidth - margin) x = rect.left - tw - margin;
    if (x < margin) x = margin;
    // 셀 상단 정렬, 화면 밖으로 나가면 위로 보정
    let y = rect.top;
    if (y + th > window.innerHeight - margin) y = window.innerHeight - th - margin;
    if (y < margin) y = margin;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }}
  function showTipFor(cell) {{
    if (cell === activeCell && tip.classList.contains('hm-visible')) return;
    activeCell = cell;
    const ag = cell.dataset.agency;
    const wk = cell.dataset.week;
    const wkLabel = cell.dataset.weekLabel;
    const launches = cells.get(ag + '||' + wk) || [];
    tip.innerHTML = renderTip(ag, wkLabel, launches);
    tip.classList.add('hm-visible');
    positionTip(cell);
    // 핀 토글 — 같은 tooltip 안에서 매번 새로 바인딩
    const pinBtn = document.getElementById('hmTipPin');
    if (pinBtn) {{
      if (pinned) pinBtn.classList.add('hm-pinned');
      pinBtn.addEventListener('click', (ev) => {{
        ev.stopPropagation();
        pinned = !pinned;
        pinBtn.classList.toggle('hm-pinned', pinned);
        pinBtn.textContent = pinned ? '📌 고정됨 (클릭 해제)' : '📌 핀';
      }});
    }}
  }}
  // 마우스: 셀 진입 시 표시, 셀 ↔ tooltip 사이 이동 허용 (300ms grace)
  grid.addEventListener('mouseover', (e) => {{
    const cell = e.target.closest('.hm-cell');
    if (!cell || cell.classList.contains('hm-zero')) return;
    clearHideTimer();
    showTipFor(cell);
  }});
  grid.addEventListener('mouseout', (e) => {{
    const cell = e.target.closest('.hm-cell');
    if (!cell || cell.classList.contains('hm-zero')) return;
    const next = e.relatedTarget;
    // 다른 데이터 셀이나 tooltip 으로 이동하면 숨기지 않음
    if (next && next instanceof Element &&
        (next.closest('.hm-cell:not(.hm-zero)') || next.closest('.hm-tooltip'))) return;
    scheduleHide(220);
  }});
  // tooltip 으로 들어오면 hide 취소, 나가면 다시 schedule
  tip.addEventListener('mouseenter', clearHideTimer);
  tip.addEventListener('mouseleave', (e) => {{
    const next = e.relatedTarget;
    if (next && next instanceof Element && next.closest('.hm-cell:not(.hm-zero)')) return;
    scheduleHide(180);
  }});
  // 보조 dismissal: 스크롤/탭전환/포커스아웃/Esc/다른 탭 클릭
  // ⚠ scroll 핸들러 — tooltip 내부 스크롤(.hm-tooltip-body) 은 절대 hide 트리거 금지.
  //    핀 상태일 때는 페이지 스크롤도 hide 안함 (사용자가 의도적으로 고정한 카드).
  window.addEventListener('scroll', (e) => {{
    const t = e.target;
    // 1) tooltip 내부에서 발생한 스크롤은 완전 무시
    if (t === tip) return;
    if (t instanceof Element && t.closest && t.closest('.hm-tooltip')) return;
    // 2) 핀 고정 상태에서는 페이지 스크롤도 hide 하지 않음
    if (pinned) return;
    hideTip(false);
  }}, true);
  window.addEventListener('blur', () => {{ if (!pinned) hideTip(false); }});
  document.addEventListener('visibilitychange', () => {{ if (document.hidden && !pinned) hideTip(false); }});
  document.addEventListener('keydown', (e) => {{
    if (e.key === 'Escape') {{ pinned = false; hideTip(true); }}
  }});
  document.querySelectorAll('nav.tabs button').forEach(b => b.addEventListener('click', () => hideTip(true)));
  // 외부 클릭으로 핀 해제
  document.addEventListener('click', (e) => {{
    if (!pinned) return;
    if (e.target.closest('.hm-tooltip') || e.target.closest('.hm-cell:not(.hm-zero)')) return;
    pinned = false;
    hideTip(true);
  }});

  }} // end if (ups.length)

  // upcoming table (Tabulator) — confirmed + 미정 모두 포함, net_sortable 로 정렬, 정밀도 표기
  // ups = D.upcoming_90 (≤90일, 미정 일부 포함). 추가로 D.upcoming_indeterminate (월/분기/연 미정 전부) 합치기.
  const allUpcoming = [...(D.upcoming_90 || []), ...(D.upcoming_indeterminate || [])];
  // 중복 제거 (id 기준)
  const seen = new Set();
  const dedup = [];
  for (const r of allUpcoming) {{
    const key = r.id || (r.net + '||' + (r.mission_name||r.name||''));
    if (seen.has(key)) continue;
    seen.add(key);
    dedup.push(r);
  }}
  // 예정 발사 표시는 JST(UTC+9) — 데이터(net)는 UTC SSOT 그대로, 표시만 변환 (2026-06-10 사용자 요청)
  // 월/분기/연 미정 라벨("2026-Q3 (분기 미정)" 등)과 "(시간 미정)"(날짜만, 시각 무의미)은 변환 없이 유지.
  function jstLabel(r) {{
    const p = r.net_precision || 'unknown';
    const net = r.net || '';
    const lbl = r.net_label || '';
    if ((p === 'confirmed' || p === 'day') && !lbl.includes('시간 미정') && net.length >= 16) {{
      const dt = new Date(net);
      if (!isNaN(dt)) {{
        const j = new Date(dt.getTime() + 9 * 3600 * 1000);
        const pad2 = n => String(n).padStart(2, '0');
        const s = `${{j.getUTCFullYear()}}-${{pad2(j.getUTCMonth() + 1)}}-${{pad2(j.getUTCDate())}} ${{pad2(j.getUTCHours())}}:${{pad2(j.getUTCMinutes())}}`;
        return lbl.includes('잠정') ? `${{s}} JST (잠정)` : `${{s}} JST`;
      }}
    }}
    return lbl || net.replace('T', ' ').slice(0, 16);
  }}
  if (dedup.length) new Tabulator('#upcomingTable', {{
    data: dedup.map(r => ({{
      net_sortable: r.net_sortable || r.net || '',
      net_display: jstLabel(r),
      precision: r.net_precision || 'unknown',
      company: r.company,
      rocket: r.rocket_model,
      pad: r.pad_name,
      mission: r.mission_name || r.name,
      customer: r.customer || '—',
      axes: (r.axes||[]).map(a=>AXIS_LABEL[a]||a).join(', '),
    }})),
    layout:'fitColumns', height:420, theme:'midnight',
    initialSort: [{{column: 'net_sortable', dir: 'asc'}}],
    columns: [
      {{title:'NET (JST)', field:'net_sortable', width:170, sorter:'string',
        formatter: (cell) => {{
          const row = cell.getRow().getData();
          return `<span style="font-size:12px">${{row.net_display||cell.getValue()}}</span>`;
        }}}},
      {{title:'정밀도', field:'precision', width:90, headerFilter:'list',
        headerFilterParams:{{values:{{'':'all','confirmed':'확정','day':'일 미정','month':'월 미정','quarter':'분기 미정','year':'연 미정'}}}},
        formatter: (cell) => {{
          const v = cell.getValue();
          const colors = {{confirmed:'#1f6f3a', day:'#3a5fcd', month:'#8a6d20', quarter:'#a06020', year:'#8b1a1a'}};
          const labels = {{confirmed:'확정', day:'일', month:'월', quarter:'분기', year:'연'}};
          return `<span class="badge" style="background:${{colors[v]||'#444'}};font-size:10px">${{labels[v]||v}}</span>`;
        }}}},
      {{title:'기업', field:'company', width:140, headerFilter:'input'}},
      {{title:'로켓', field:'rocket', width:160, headerFilter:'input'}},
      {{title:'발사대', field:'pad', width:180}},
      {{title:'미션', field:'mission', headerFilter:'input'}},
      {{title:'고객', field:'customer', width:170, headerFilter:'input',
        formatter: (cell) => `<span style="font-size:12px">${{cell.getValue()||'—'}}</span>`}},
      {{title:'4축', field:'axes', width:160}},
    ],
  }});
}});

// ── Tab 3: 이력 + 도넛 ─────────────────────────────────
tsrInit(function () {{
  const hist = D.history || [];
  // 도넛
  const slugs = {{spacex:'SpaceX', rocketlab:'Rocket Lab', blueorigin:'Blue Origin'}};
  const targets = {{spacex:'donutSpaceX', rocketlab:'donutRocketLab', blueorigin:'donutBlueOrigin'}};
  Object.entries(slugs).forEach(([slug, label]) => {{
    const subset = hist.filter(r => r._slug === slug && r.result);
    const counts = {{success:0, failure:0, partial:0}};
    subset.forEach(r => {{ counts[r.result] = (counts[r.result]||0) + 1; }});
    const canvas = document.getElementById(targets[slug]);
    if (!canvas) return;
    CV.Donut(canvas, {{
      title: `${{label}} 성공률 (n=${{subset.length}})`,
      hole: 0.55,
      slices: [
        {{label:'성공', value:counts.success, color:'#1f6f3a'}},
        {{label:'실패', value:counts.failure, color:'#8b1a1a'}},
        {{label:'부분', value:counts.partial, color:'#8a6d20'}},
      ],
    }});
  }});

  // history table — net_sortable 정렬, net_label 표시 (이력은 보통 confirmed 지만 분류 일관성 유지)
  const rows = hist.map(r => ({{
    date_sortable: r.net_sortable || r.net || '',
    date_display: r.net_label || (r.net||'').slice(0,10),
    precision: r.net_precision || 'confirmed',
    company: r.company,
    rocket: r.rocket_model,
    pad: r.pad_name,
    mission: r.mission_name || r.name,
    customer: r.customer,
    axes: (r.axes||[]).map(a=>AXIS_LABEL[a]||a).join(', '),
    result: r.result,
  }})).sort((a,b) => (b.date_sortable||'').localeCompare(a.date_sortable||''));

  new Tabulator('#historyTable', {{
    data: rows,
    layout:'fitColumns', height:560, theme:'midnight',
    pagination:'local', paginationSize: 25,
    initialSort:[{{column:'date_sortable', dir:'desc'}}],
    columns: [
      {{title:'날짜 (UTC)', field:'date_sortable', width:120, headerFilter:'input', sorter:'string',
        formatter: (cell) => {{
          const row = cell.getRow().getData();
          return `<span style="font-size:12px">${{row.date_display||cell.getValue()}}</span>`;
        }}}},
      {{title:'기업', field:'company', width:150, headerFilter:'list', headerFilterParams:{{valuesLookup:true, clearable:true}}}},
      {{title:'로켓', field:'rocket', width:170, headerFilter:'input'}},
      {{title:'발사대', field:'pad', width:180, headerFilter:'input'}},
      {{title:'미션', field:'mission', headerFilter:'input'}},
      {{title:'고객', field:'customer', width:180, headerFilter:'input',
        formatter: (cell) => `<span style="font-size:12px">${{cell.getValue()||'—'}}</span>`}},
      {{title:'4축', field:'axes', width:160, headerFilter:'input'}},
      {{title:'결과', field:'result', width:110, headerFilter:'list',
        headerFilterParams:{{values:{{'':'전체','success':'성공','failure':'실패','partial':'부분'}}}},
        formatter: (cell) => {{
          const v = cell.getValue();
          if (v === 'success') return '<span class="badge green">성공</span>';
          if (v === 'failure') return '<span class="badge red">실패</span>';
          if (v === 'partial') return '<span class="badge amber">부분</span>';
          return '<span class="badge gray">—</span>';
        }}
      }},
    ],
  }});
}});

// ── Tab 4: 인사이트 ───────────────────────────────────
tsrInit(function () {{
  const yoy = D.metrics.yoy || {{}};
  // grouped bar — 연도별 발사 횟수
  const allYears = new Set();
  Object.values(yoy).forEach(rows => rows.forEach(r => allYears.add(r.year)));
  const years = [...allYears].sort();
  // 연도별 발사 횟수 — Canvas StackedBar (3사 누적)
  (function() {{
    const canvas = document.getElementById('yoyBar');
    if (!canvas) return;
    const seriesOrder = ['spacex', 'rocketlab', 'blueorigin'];
    const series = seriesOrder.filter(s => yoy[s]).map(slug => {{
      const map = Object.fromEntries((yoy[slug]||[]).map(r => [r.year, r.count]));
      return {{
        name: slug,
        color: colorFor(slug),
        values: years.map(y => map[y] || 0),
      }};
    }});
    CV.StackedBar(canvas, {{categories: years, series, yLabel: '발사 횟수 (연도)'}});
  }})();

  // 성공률 라인 (연도별, 기업별) — Canvas MultiLine
  const hist = D.history || [];
  (function() {{
    const canvas = document.getElementById('successLine');
    if (!canvas) return;
    const lines = {{}};
    hist.forEach(r => {{
      const y = (r.net||'').slice(0,4);
      if (!y || !r.result || !r._slug) return;
      if (!lines[r._slug]) lines[r._slug] = {{}};
      if (!lines[r._slug][y]) lines[r._slug][y] = {{s:0, t:0}};
      lines[r._slug][y].t += 1;
      if (r.result === 'success') lines[r._slug][y].s += 1;
    }});
    const allYears = new Set();
    Object.values(lines).forEach(ymap => Object.keys(ymap).forEach(y => allYears.add(y)));
    const xs = [...allYears].sort();
    const seriesOrder = ['spacex', 'rocketlab', 'blueorigin'];
    const series = seriesOrder.filter(s => lines[s]).map(slug => ({{
      name: slug,
      color: colorFor(slug),
      values: xs.map(y => lines[slug][y] ? Math.round(lines[slug][y].s/lines[slug][y].t*1000)/10 : null),
    }}));
    CV.MultiLine(canvas, {{xs, series, yMin:0, yMax:105}});
  }})();

  // 4축 미션 분포 — Canvas Donut
  const ax = (D.metrics.axis_distribution && D.metrics.axis_distribution.overall) || {{}};
  (function() {{
    const canvas = document.getElementById('axisPie');
    if (!canvas) return;
    const slices = Object.entries(ax).map(([k, v]) => ({{
      label: AXIS_LABEL[k] || k,
      value: v,
      color: AXIS_COLOR[k] || '#666',
    }}));
    CV.Donut(canvas, {{
      title: '4축 미션 분포 (history + upcoming)',
      slices,
      hole: 0.55,
      centerLabel: true,
    }});
  }})();

  // 🤖 AI 해석 — 4축 분포 기반 현재 브리핑 + 전망
  (function renderAxisInsight() {{
    const host = document.getElementById('axisInsight');
    if (!host) return;
    const total = Object.values(ax).reduce((a,b) => a+(b||0), 0);
    if (!total) {{ host.innerHTML = '<p class="muted">데이터 없음</p>'; return; }}
    // 정렬: 점유 비율 desc
    const sorted = Object.entries(ax).sort((a,b) => (b[1]||0) - (a[1]||0));
    const NARRATIVE = {{
      territory: {{
        now: 'NASA Artemis HLS 사전 검증(Blue Moon Pathfinder), Lunar Gateway 모듈 발사가 본격화되며 달·심우주 자원 축이 정부 자금을 흡수. LMT/NOC/MAXAR + RKLB(VIPER 후속) 인접 매출 라인이 부상.',
        future: '2027~2030 Artemis III/IV 유인 착륙, 달 표면 자원(ISRU) 시연, 화성 샘플 회수가 차세대 율속. 우주조약(Outer Space Treaty) 재해석 + 미·중 달 자원 권리 프레임워크 협상이 정책 변수.',
      }},
      comms: {{
        now: 'LEO 통신 컨스텔레이션이 발사 cadence 의 주력. SpaceX Starlink + Amazon Kuiper + Telesat Lightspeed + AST SpaceMobile 4파전, 통신사·정부 양면 채널 동시 가속.',
        future: 'Direct-to-cell(ASTS·Starlink D2C)이 기존 통신사 매출 잠식. 6G 표준(3GPP NTN) 의무 사양 진입 시 발사 수요 추가 가속. Telesat/Viasat 등 GEO 사업자는 전환 압력 가중.',
      }},
      energy_logistics: {{
        now: '발사·운송 인프라 자체가 4축의 율속 단계. Falcon 9 cadence(분기당 30~40회) + Starship V3 데뷔(2026-Q2)가 전체 시나리오의 토대. $/kg 우위가 누적 락인 효과.',
        future: 'Starship V3 의 정상 cadence(월 1~2회) 도달 시 $/kg 추가 1/10 인하 트리거 → 컨스텔레이션·달·화성 모든 시나리오 가속. 궤도상 연료 보급(orbital refueling) 시연이 다음 마일스톤.',
      }},
      defense: {{
        now: '미군 NSSL Phase 3 Lane 1/2 + SDA Tranche 1/2 + Starshield + HASTE 극초음속 시험. RTX/LMT/NOC/RKLB 의 정부 매출 비중 확대로 멀티플 안정화. PLTR/BAH/CACI 인접 SI 수혜.',
        future: 'Golden Dome 미사일 방어망(EO 14186), 우주 도메인 인지(SDA) Tranche 3 발주, 위성 인터셉터 ATTR 가 차세대 방위비 흡수처. 미·중 우주 군사화 경쟁이 2027~2030 발주 가속.',
      }},
    }};
    const top = sorted[0];
    const second = sorted[1];
    const topName = AXIS_LABEL[top[0]] || top[0];
    const topPct = (top[1]/total*100).toFixed(1);
    const secondName = second ? (AXIS_LABEL[second[0]] || second[0]) : null;
    const secondPct = second ? (second[1]/total*100).toFixed(1) : null;
    const rankPills = sorted.map(([k,v]) => {{
      const c = D.axis_color[k] || '#666';
      return `<span class="ai-axis-rank-item" style="background:${{c}}">${{AXIS_LABEL[k]||k}} ${{(v/total*100).toFixed(0)}}%</span>`;
    }}).join(' ');
    const topNarr = NARRATIVE[top[0]] || {{now:'-', future:'-'}};
    const secondNarr = second ? (NARRATIVE[second[0]] || {{now:'-', future:'-'}}) : null;
    host.innerHTML = `
      <div class="ai-headline">📌 <b>${{topName}}</b> 축이 전체의 <b>${{topPct}}%</b> 점유로 최우선 — ${{secondName ? `2위 ${{secondName}} ${{secondPct}}%` : ''}}</div>
      <div class="ai-section">
        <h3>📊 현재 브리핑</h3>
        <p>${{topNarr.now}}</p>
        ${{secondNarr ? `<div class="ai-secondary"><b>+ ${{secondName}}:</b> ${{secondNarr.now}}</div>` : ''}}
      </div>
      <div class="ai-section">
        <h3>🔮 앞으로의 전망</h3>
        <p>${{topNarr.future}}</p>
        ${{secondNarr ? `<div class="ai-secondary"><b>+ ${{secondName}}:</b> ${{secondNarr.future}}</div>` : ''}}
      </div>
      <div class="ai-axis-rank">${{rankPills}}</div>`;
  }})();

}});

// ── Mission Insight (mi- prefix): 섹터 탭 + 미션 카드 + 타임라인 ─────
tsrInit(function () {{
  // data/rocket/missions.json (큐레이션 파일) 에서 로드. 누락 시 빈 배열.
  const MISSIONS = (D.missions || []);
  if (!MISSIONS.length) console.warn('[TSR] D.missions 비어있음 — data/rocket/missions.json 확인 필요');

  const SECTORS = ['전체', '달·심우주', '통신 인프라', '지구관측', '국방·안보', '우주정거장'];
  const SECTOR_COLOR = {{
    '달·심우주':   {{bg:'#E6F1FB', fg:'#185FA5'}},
    '통신 인프라': {{bg:'#E1F5EE', fg:'#0F6E56'}},
    '지구관측':    {{bg:'#FAEEDA', fg:'#854F0B'}},
    '국방·안보':   {{bg:'#FCEBEB', fg:'#A32D2D'}},
    '우주정거장':  {{bg:'#EEEDFE', fg:'#534AB7'}},
  }};
  const STATUS_KEY = (s) => String(s||'').replace(/\s+/g,'');  // '진행 중' → '진행중' for class

  let activeSector = '전체';
  let activeMissionId = null;

  // 공유 tooltip (body 직속)
  let tip = document.getElementById('mi-tooltip');
  if (!tip) {{
    tip = document.createElement('div');
    tip.id = 'mi-tooltip';
    tip.className = 'mi-tooltip';
    document.body.appendChild(tip);
  }}
  function showTip(html, x, y) {{
    tip.innerHTML = html;
    tip.classList.add('mi-visible');
    moveTip(x, y);
  }}
  function moveTip(cx, cy) {{
    const margin = 14, tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = cx + margin, y = cy + margin;
    if (x + tw > window.innerWidth - 8) x = cx - tw - margin;
    if (y + th > window.innerHeight - 8) y = cy - th - margin;
    if (x < 8) x = 8; if (y < 8) y = 8;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }}
  function hideTip() {{ tip.classList.remove('mi-visible'); }}

  function filteredMissions() {{
    return activeSector === '전체' ? MISSIONS : MISSIONS.filter(m => m.sector === activeSector);
  }}

  // ── BLOCK 1: 섹터 탭 + 미션 카드 ───────────────────────────────
  const tabsHost = document.getElementById('miTabs');
  const gridHost = document.getElementById('miGrid');

  function renderTabs() {{
    tabsHost.innerHTML = SECTORS.map(s =>
      `<button class="mi-tab ${{s === activeSector ? 'mi-active' : ''}}" data-sector="${{s}}" role="tab">${{s}}</button>`
    ).join('');
  }}

  function escapeHtml(s) {{
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]
    );
  }}

  function renderCards() {{
    const items = filteredMissions();
    gridHost.innerHTML = items.length ? items.map(m => {{
      const c = SECTOR_COLOR[m.sector] || {{bg:'#2a3340', fg:'#aaa'}};
      const stKey = STATUS_KEY(m.status);
      return `
        <div class="mi-card ${{m.id === activeMissionId ? 'mi-card-active' : ''}}" data-mid="${{m.id}}">
          <span class="mi-sector-pill" style="background:${{c.bg}};color:${{c.fg}}">${{escapeHtml(m.sector)}}</span>
          <div class="mi-name">${{escapeHtml(m.name)}}</div>
          <div class="mi-summary">${{escapeHtml(m.summary)}}</div>
          <div class="mi-divider"></div>
          <div class="mi-meta">
            <div class="mi-meta-row">🚀 <b>${{escapeHtml(m.rocket)}}</b></div>
            <div class="mi-meta-row">🏛️ <b>${{escapeHtml(m.customer)}}</b></div>
            <div class="mi-meta-row">📅 <b>${{m.year}}</b><span class="mi-status-pill mi-status-${{stKey}}">${{escapeHtml(m.status)}}</span></div>
          </div>
        </div>`;
    }}).join('') : '<div class="muted" style="padding:20px">해당 섹터 미션 없음</div>';
  }}

  // 위임된 클릭 — 탭과 카드 모두
  document.getElementById('mi-section').addEventListener('click', (e) => {{
    const tab = e.target.closest('.mi-tab');
    if (tab) {{
      activeSector = tab.dataset.sector;
      activeMissionId = null;
      renderTabs(); renderCards(); renderTimeline();
      return;
    }}
    const card = e.target.closest('.mi-card');
    if (card) {{
      activeMissionId = card.dataset.mid;
      renderCards(); renderTimeline();
      // 타임라인 마커 위치로 부드럽게 스크롤 (사용자가 강조 확인 가능)
      const marker = document.querySelector(`#miTimeline [data-mid="${{activeMissionId}}"]`);
      if (marker) marker.scrollIntoView({{behavior:'smooth', block:'nearest'}});
    }}
  }});

  // ── BLOCK 2: 타임라인 (SVG, 반응형) ─────────────────────────────
  const svg = document.getElementById('miTimeline');
  const Y_MIN = 2020, Y_MAX = 2030;

  function renderTimeline() {{
    if (!svg) return;
    const wrap = svg.parentElement;
    const W = wrap.getBoundingClientRect().width || 800;
    const H = 160;
    const PAD = {{l:30, r:30, t:20, b:30}};
    svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);

    const xOf = (year) => PAD.l + (year - Y_MIN) / (Y_MAX - Y_MIN) * (W - PAD.l - PAD.r);
    const baseY = H - PAD.b - 30;

    let html = '';
    // 축 라인
    html += `<line class="mi-axis-line" x1="${{PAD.l}}" y1="${{baseY}}" x2="${{W-PAD.r}}" y2="${{baseY}}"/>`;
    // 연도 눈금 (2020 ~ 2030)
    for (let y = Y_MIN; y <= Y_MAX; y++) {{
      const x = xOf(y);
      html += `<line class="mi-tick" x1="${{x}}" y1="${{baseY-4}}" x2="${{x}}" y2="${{baseY+4}}"/>`;
      html += `<text class="mi-tick-label" x="${{x}}" y="${{baseY+18}}" text-anchor="middle">${{y}}</text>`;
    }}
    // 오늘 선
    const now = new Date();
    const nowYear = now.getUTCFullYear() + (now.getUTCMonth()/12) + (now.getUTCDate()/365);
    if (nowYear >= Y_MIN && nowYear <= Y_MAX) {{
      const tx = xOf(nowYear);
      html += `<line class="mi-today-line" x1="${{tx}}" y1="${{PAD.t}}" x2="${{tx}}" y2="${{baseY}}"/>`;
      html += `<text class="mi-today-label" x="${{tx+4}}" y="${{PAD.t+10}}">오늘</text>`;
    }}

    // 미션 마커 — 같은 연도 충돌 시 위/아래 교대 + Y 오프셋
    const items = filteredMissions().slice().sort((a,b) => a.year - b.year);
    const yearStack = new Map();
    items.forEach((m, idx) => {{
      const cnt = (yearStack.get(m.year) || 0);
      yearStack.set(m.year, cnt + 1);
      const above = (cnt % 2 === 0);
      const yOff = Math.floor(cnt / 2) * 32;
      const cy = above ? (baseY - 16 - yOff) : (baseY + 16 + yOff);
      const labelY = above ? (cy - 10) : (cy + 16);
      const cx = xOf(m.year);
      const c = SECTOR_COLOR[m.sector] || {{bg:'#2a3340', fg:'#aaa'}};
      const isActive = m.id === activeMissionId;
      const r = isActive ? 9 : 6;
      const strokeW = isActive ? 3 : 1.5;
      html += `<g class="mi-marker ${{isActive ? 'mi-marker-active' : ''}}" data-mid="${{m.id}}" data-year="${{m.year}}" transform="translate(${{cx}}, ${{cy}})">
        <circle r="${{r}}" fill="${{c.fg}}" stroke="#fff" stroke-width="${{strokeW}}"/>
      </g>`;
      // 라벨 — 좁을 때 줄임
      const label = m.name.length > 14 ? m.name.slice(0, 13) + '…' : m.name;
      html += `<text class="mi-label" x="${{cx}}" y="${{labelY}}" text-anchor="middle" data-mid="${{m.id}}">${{escapeHtml(label)}}</text>`;
    }});
    svg.innerHTML = html;
  }}

  // SVG 마커 hover (위임)
  svg.addEventListener('mouseover', (e) => {{
    const g = e.target.closest('.mi-marker');
    if (!g) return;
    const m = MISSIONS.find(x => x.id === g.dataset.mid);
    if (!m) return;
    const html = `
      <div class="mi-tooltip-title">${{escapeHtml(m.name)}}</div>
      <div class="mi-tooltip-row"><span>섹터</span><b>${{escapeHtml(m.sector)}}</b></div>
      <div class="mi-tooltip-row"><span>로켓</span><b>${{escapeHtml(m.rocket)}}</b></div>
      <div class="mi-tooltip-row"><span>고객</span><b>${{escapeHtml(m.customer)}}</b></div>
      <div class="mi-tooltip-row"><span>날짜</span><b>${{m.year}} · ${{escapeHtml(m.status)}}</b></div>
      <div style="margin-top:6px;color:var(--muted);font-size:11px">${{escapeHtml(m.summary)}}</div>`;
    showTip(html, e.clientX, e.clientY);
  }});
  svg.addEventListener('mousemove', (e) => {{
    if (!tip.classList.contains('mi-visible')) return;
    moveTip(e.clientX, e.clientY);
  }});
  svg.addEventListener('mouseout', (e) => {{
    const next = e.relatedTarget;
    if (next && next instanceof Element && next.closest && next.closest('.mi-marker')) return;
    hideTip();
  }});

  // ResizeObserver: 타임라인 반응형
  if (typeof ResizeObserver !== 'undefined' && svg) {{
    new ResizeObserver(() => renderTimeline()).observe(svg.parentElement);
  }}
  window.addEventListener('resize', renderTimeline);

  // 첫 렌더
  renderTabs();
  renderCards();
  renderTimeline();

  // 인사이트 탭 활성화 시 SVG 너비 재계산
  document.getElementById('tabs').addEventListener('click', (e) => {{
    if (e.target && e.target.dataset && e.target.dataset.tab === 't-stock') {{
      requestAnimationFrame(() => requestAnimationFrame(renderTimeline));
    }}
  }});
}});

// ── Tab 2: 로켓 실루엣 + 버블 차트 (rs-/bc- 컴포넌트) ───────────────
tsrInit(function () {{
  // data/rocket/rocket_specs.json (큐레이션 + metrics 동적 매핑) 에서 로드. 누락 시 빈 배열.
  const ROCKET_DEFAULT = (D.rocket_specs_curated || []);
  if (!ROCKET_DEFAULT.length) console.warn('[TSR] D.rocket_specs_curated 비어있음 — data/rocket/rocket_specs.json 확인 필요');

  // curated_specs 가 있으면 그쪽을 우선 채택 (사양 정확도 ↑) — 부족 필드는 default 로 보강
  const NAME_MAP = {{
    'Falcon 9 Block 5': 'Falcon 9 B5',
    'Starship V3 / Super Heavy Block 3': 'Starship V3',
    'LVM-3 (formerly GSLV Mark III)': 'LVM-3',
    'HASTE (Hypersonic Accelerator Suborbital Test Electron)': 'HASTE',
  }};
  const SHORT_MAP = {{
    'Starship V3':'SS','Falcon Heavy':'FH','New Glenn':'NG','Falcon 9 B5':'F9',
    'Neutron':'N','LVM-3':'LVM3','Electron':'E','New Shepard':'NS','HASTE':'HS'
  }};
  const COLOR_MAP = {{
    'Starship V3':'#185FA5','Falcon Heavy':'#378ADD','New Glenn':'#1D9E75','Falcon 9 B5':'#534AB7',
    'Neutron':'#BA7517','LVM-3':'#639922','Electron':'#D85A30','New Shepard':'#888780','HASTE':'#a5a3a0'
  }};
  function pickLeo(s) {{
    if (typeof s.leo_capacity_kg === 'number') return s.leo_capacity_kg;
    if (s.leo_capacity_kg && typeof s.leo_capacity_kg === 'object')
      return s.leo_capacity_kg.expended || s.leo_capacity_kg.downrange_landing || s.leo_capacity_kg.rtls_landing || null;
    if (typeof s.leo_capacity_kg_reusable === 'number') return s.leo_capacity_kg_reusable;
    if (typeof s.leo_capacity_kg_expended === 'number') return s.leo_capacity_kg_expended;
    if (typeof s.leo_capacity_t === 'number') return s.leo_capacity_t * 1000;
    return null;
  }}
  function pickHeight(s) {{ return s.height_m || s.combined_height_m || s.height_m_plus || null; }}
  function pickReuse(s) {{
    if (s.reusable === true) return true;
    if (s.reusable === false) return false;
    const v = String(s.reusable || '').toLowerCase();
    return v.startsWith('full') || v.startsWith('partial');
  }}
  function pickLaunches(s, fallback) {{
    if (typeof s.cumulative_launches_2026_04 === 'number') return s.cumulative_launches_2026_04;
    if (typeof s.cumulative_launches_2025_12_24 === 'number') return s.cumulative_launches_2025_12_24;
    const m = String(s.block_5_launch_count_2026_04 || '').match(/\d+/);
    if (m) return parseInt(m[0], 10);
    return fallback;
  }}

  const cs = D.curated_specs || [];
  let ROCKETS;
  if (cs.length) {{
    const byName = new Map();
    for (const s of cs) {{
      const raw = s.model || '';
      const name = NAME_MAP[raw] || raw;
      if (!name || name === 'HASTE') continue;          // suborbital testbed 제외
      const def = ROCKET_DEFAULT.find(d => d.name === name) || {{}};
      const h = pickHeight(s) || def.h || null;
      if (!h) continue;
      byName.set(name, {{
        name,
        shortName: SHORT_MAP[name] || name.slice(0,4),
        h: Math.round(h),
        leo: pickLeo(s) ?? def.leo ?? null,
        gto: s.gto_capacity_kg || (s.gto_capacity_t_plus ? s.gto_capacity_t_plus * 1000 : (def.gto || null)),
        costKg: s.launch_cost_usd_per_kg_LEO_estimated || def.costKg || null,
        reuse: pickReuse(s),
        color: COLOR_MAP[name] || def.color || '#888',
        launches: pickLaunches(s, def.launches || 0),
        successRate: (typeof s.success_rate_pct === 'number') ? s.success_rate_pct : (def.successRate ?? null),
        status: s.operational_status_2026 ? '운용 정지' :
                (String(s.first_launch || s.first_flight_v3 || '').includes('NET') ? '개발 중' : (def.status || '운용 중')),
        stages: def.stages || null,   // curated 에는 단별 비율 메타가 없으므로 default 그대로 차용
      }});
    }}
    // ROCKET_DEFAULT 의 누락된 항목 보강
    for (const d of ROCKET_DEFAULT) if (!byName.has(d.name)) byName.set(d.name, d);
    ROCKETS = [...byName.values()].sort((a,b) => b.h - a.h);
  }} else {{
    ROCKETS = ROCKET_DEFAULT.slice().sort((a,b) => b.h - a.h);
  }}

  // ── 공유 tooltip (실루엣 + 버블 차트 둘 다 사용) ──
  let tooltip = document.getElementById('rs-tooltip');
  if (!tooltip) {{
    tooltip = document.createElement('div');
    tooltip.id = 'rs-tooltip';
    tooltip.className = 'rs-tooltip';
    document.body.appendChild(tooltip);
  }}
  function fmtKg(v) {{ return v != null ? v.toLocaleString() + ' kg' : 'TBD'; }}
  function fmtCost(v) {{ return v != null ? '$' + v.toLocaleString() : '비공개'; }}
  function showTooltip(html, clientX, clientY) {{
    tooltip.innerHTML = html;
    tooltip.classList.add('rs-visible');
    moveTooltip(clientX, clientY);
  }}
  function moveTooltip(cx, cy) {{
    const margin = 14;
    const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    let x = cx + margin, y = cy + margin;
    if (x + tw > window.innerWidth - 8) x = cx - tw - margin;
    if (y + th > window.innerHeight - 8) y = cy - th - margin;
    if (x < 8) x = 8;
    if (y < 8) y = 8;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }}
  function hideTooltip() {{ tooltip.classList.remove('rs-visible'); }}

  // ── 1) 실루엣 렌더 ──────────────────────────────────────────────
  function renderSilhouette() {{
    const stage = document.getElementById('rsStage');
    const foot  = document.getElementById('rsFootRow');
    if (!stage || !foot) return;
    stage.innerHTML = '';
    foot.innerHTML = '';
    const STAGE_BAR_MAX = 180;            // px (사양: 180)
    const maxH = Math.max(...ROCKETS.map(r => r.h));
    for (const r of ROCKETS) {{
      const px = Math.max(8, Math.round((r.h / maxH) * STAGE_BAR_MAX));
      const col = document.createElement('div');
      col.className = 'rs-col';
      const leo = document.createElement('div');
      leo.className = 'rs-leo-tag';
      leo.textContent = r.leo != null ? (r.leo >= 1000 ? Math.round(r.leo/1000)+'t' : r.leo+'kg') : 'TBD';
      const bar = document.createElement('div');
      bar.className = 'rs-bar';
      bar.style.height = px + 'px';
      bar.style.background = 'transparent';   // 단별 segment 가 채움
      bar.dataset.rocket = r.name;
      // ── 각 단을 아래(0%)에서 위로 적층, 단마다 reuse 여부에 따라 opacity·줄무늬 분기 ──
      const stages = (r.stages && r.stages.length) ? r.stages
                  : [{{label:'전체', ratio:1.0, reuse:!!r.reuse}}];
      let cumBottom = 0;
      stages.forEach((st, idx) => {{
        const seg = document.createElement('div');
        seg.className = 'rs-stage-seg ' + (st.reuse ? 'rs-reuse' : 'rs-expend');
        seg.style.bottom = (cumBottom * 100) + '%';
        seg.style.height = (st.ratio * 100) + '%';
        seg.style.setProperty('--seg-color', r.color);
        seg.style.backgroundColor = r.color;
        bar.appendChild(seg);
        cumBottom += st.ratio;
        // 단 사이 구분선 (마지막 단 위에는 그리지 않음)
        if (idx < stages.length - 1) {{
          const div = document.createElement('div');
          div.className = 'rs-stage-divider';
          div.style.bottom = (cumBottom * 100) + '%';
          bar.appendChild(div);
        }}
      }});
      col.appendChild(leo);
      col.appendChild(bar);
      stage.appendChild(col);

      // 푸터: 이름 + 높이 + 단별 dot 범례
      const fc = document.createElement('div');
      fc.className = 'rs-foot-col';
      const dots = stages.map(st =>
        `<span class="rs-stage-dot ${{st.reuse ? '' : 'rs-expend'}}" style="background-color:${{r.color}}" title="${{st.label}} — ${{st.reuse ? '재사용' : '소모'}}"></span>`
      ).join('');
      fc.innerHTML = `<div class="rs-name">${{r.name}}</div><div class="rs-h">${{r.h}}m</div><div class="rs-stage-legend">${{dots}}</div>`;
      foot.appendChild(fc);
    }}
    // hover delegation on stage
    stage.addEventListener('mouseover', (e) => {{
      const bar = e.target.closest('.rs-bar');
      if (!bar) return;
      const r = ROCKETS.find(x => x.name === bar.dataset.rocket);
      if (!r) return;
      // 단별 재사용 요약 (위→아래 순서로 표기 = 시각상 위에서부터)
      const stages = (r.stages && r.stages.length) ? r.stages : [];
      const stageRows = stages.slice().reverse().map((st, i) =>
        `<div class="rs-tooltip-row"><span>${{stages.length - i}}단 ${{st.label||''}}</span><b style="color:${{st.reuse ? '#7ee787' : '#ffa657'}}">${{st.reuse ? '✅ 재사용' : '🔻 소모'}}</b></div>`
      ).join('');
      const reuseSummary = stages.length
        ? `${{stages.filter(s => s.reuse).length}}/${{stages.length}}단 재사용`
        : (r.reuse ? '가능' : '불가');
      const html = `
        <div class="rs-tooltip-title">${{r.name}}</div>
        <div class="rs-tooltip-row"><span>높이</span><b>${{r.h}} m</b></div>
        <div class="rs-tooltip-row"><span>LEO</span><b>${{fmtKg(r.leo)}}</b></div>
        <div class="rs-tooltip-row"><span>GTO</span><b>${{fmtKg(r.gto)}}</b></div>
        <div class="rs-tooltip-row"><span>$/kg</span><b>${{fmtCost(r.costKg)}}</b></div>
        <div class="rs-tooltip-row"><span>재사용</span><b>${{reuseSummary}}</b></div>
        ${{stageRows ? `<div style="border-top:1px dashed var(--line);margin:6px 0;padding-top:4px"></div>${{stageRows}}` : ''}}
        <div class="rs-tooltip-row"><span>현재 상태</span><b>${{r.status}}</b></div>`;
      showTooltip(html, e.clientX, e.clientY);
    }});
    stage.addEventListener('mousemove', (e) => {{
      if (!tooltip.classList.contains('rs-visible')) return;
      moveTooltip(e.clientX, e.clientY);
    }});
    stage.addEventListener('mouseout', (e) => {{
      const next = e.relatedTarget;
      if (next && next instanceof Element && next.closest && next.closest('.rs-bar')) return;
      hideTooltip();
    }});
  }}

  // ── 2) 버블 차트 (Canvas) ─────────────────────────────────────────
  const canvas = document.getElementById('bcCanvas');
  const ctx = canvas ? canvas.getContext('2d') : null;
  const PAD = {{top:18, right:24, bottom:38, left:64}};
  const LOG_MIN = 100, LOG_MAX = 200000;
  const Y_MIN = 0,    Y_MAX = 30000;
  let bubbleHover = null;

  function rScale(launches) {{
    const minR = 14, maxR = 44;
    const lo = 1, hi = 500;
    const v = Math.max(lo, launches || lo);
    const t = (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
    return minR + Math.max(0, Math.min(1, t)) * (maxR - minR);
  }}
  function xScale(kg, w) {{
    const lx = Math.log10(Math.max(LOG_MIN, kg));
    const lmin = Math.log10(LOG_MIN), lmax = Math.log10(LOG_MAX);
    return PAD.left + (lx - lmin) / (lmax - lmin) * (w - PAD.left - PAD.right);
  }}
  function yScale(cost, h) {{
    return h - PAD.bottom - (cost - Y_MIN) / (Y_MAX - Y_MIN) * (h - PAD.top - PAD.bottom);
  }}

  function drawBubbles() {{
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 800;
    const h = 260;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // axis grid
    ctx.font = '12px -apple-system, "SF Pro Text", "Pretendard", sans-serif';
    ctx.textBaseline = 'middle';
    ctx.strokeStyle = 'rgba(139,148,158,0.18)';
    ctx.fillStyle = '#8b949e';
    ctx.lineWidth = 1;
    // Y gridlines
    [0, 5000, 10000, 20000, 30000].forEach(c => {{
      const y = yScale(c, h);
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
      ctx.textAlign = 'right';
      ctx.fillText('$' + c.toLocaleString(), PAD.left - 8, y);
    }});
    // X gridlines
    [100, 1000, 10000, 100000].forEach(kg => {{
      const x = xScale(kg, w);
      ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, h - PAD.bottom); ctx.stroke();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(kg.toLocaleString(), x, h - PAD.bottom + 6);
    }});
    // Axis titles
    ctx.fillStyle = '#8b949e';
    ctx.font = '12px -apple-system, "Pretendard", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText('LEO 탑재량 (kg)', PAD.left + (w - PAD.left - PAD.right)/2, h - 8);
    ctx.save();
    ctx.translate(16, PAD.top + (h - PAD.top - PAD.bottom)/2);
    ctx.rotate(-Math.PI/2);
    ctx.fillText('$/kg', 0, 0);
    ctx.restore();

    // bubbles
    const visible = ROCKETS.filter(r => r.leo != null && r.costKg != null);
    visible.forEach(r => {{
      const cx = xScale(r.leo, w);
      const cy = yScale(r.costKg, h);
      const radius = rScale(r.launches || 0);
      r._bubble = {{cx, cy, r: radius}};
      ctx.fillStyle = r.color;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI*2);
      ctx.fill();
      ctx.lineWidth = (r === bubbleHover) ? 3 : 2;
      ctx.strokeStyle = (r === bubbleHover) ? '#fff' : 'rgba(255,255,255,0.85)';
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px -apple-system, "Pretendard", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(r.shortName, cx, cy);
    }});

    // hidden list (cost 미공개)
    const hidden = ROCKETS.filter(r => r.leo != null && r.costKg == null);
    const hostHidden = document.getElementById('bcHiddenList');
    if (hostHidden) {{
      hostHidden.innerHTML = hidden.length
        ? `<b>비용 미공개 (차트 제외):</b> ` + hidden.map(r =>
            `<span class="bc-hidden-pill" style="background:${{r.color}}33;color:${{r.color}};border:1px solid ${{r.color}}66">${{r.name}}</span>`
          ).join('')
        : '';
    }}
  }}

  function bubbleAt(x, y) {{
    const visible = ROCKETS.filter(r => r._bubble);
    for (const r of visible) {{
      const dx = x - r._bubble.cx, dy = y - r._bubble.cy;
      if (dx*dx + dy*dy <= r._bubble.r * r._bubble.r) return r;
    }}
    return null;
  }}

  if (canvas && ctx) {{
    canvas.addEventListener('mousemove', (e) => {{
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const found = bubbleAt(x, y);
      if (found !== bubbleHover) {{
        bubbleHover = found;
        drawBubbles();
      }}
      if (found) {{
        const html = `
          <div class="rs-tooltip-title">${{found.name}}</div>
          <div class="rs-tooltip-row"><span>LEO</span><b>${{fmtKg(found.leo)}}</b></div>
          <div class="rs-tooltip-row"><span>$/kg</span><b>${{fmtCost(found.costKg)}}</b></div>
          <div class="rs-tooltip-row"><span>누적 발사</span><b>${{found.launches || 0}} 회</b></div>
          <div class="rs-tooltip-row"><span>성공률</span><b>${{found.successRate != null ? found.successRate + '%' : '—'}}</b></div>`;
        showTooltip(html, e.clientX, e.clientY);
      }} else {{
        hideTooltip();
      }}
    }});
    canvas.addEventListener('mouseleave', () => {{
      if (bubbleHover) {{ bubbleHover = null; drawBubbles(); }}
      hideTooltip();
    }});

    // resize observer + window resize fallback
    if (typeof ResizeObserver !== 'undefined') {{
      new ResizeObserver(() => drawBubbles()).observe(canvas.parentElement);
    }}
    window.addEventListener('resize', drawBubbles);
  }}

  // 다크모드 감지 (현 dashboard 는 항상 다크지만 사양 준수)
  const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  void isDark;  // 색상은 이미 다크 톤 — 변수 사용은 향후 라이트 테마 전환 시

  // 첫 렌더 (탭이 hidden 일 수 있어 즉시 + 탭 활성화 시 재계산)
  renderSilhouette();
  drawBubbles();
  document.getElementById('tabs').addEventListener('click', (e) => {{
    if (e.target && e.target.dataset && e.target.dataset.tab === 't-spec') {{
      // 탭이 보이게 된 다음 프레임에 재계산 (canvas 폭이 0 이었을 수 있음)
      requestAnimationFrame(() => requestAnimationFrame(drawBubbles));
    }}
  }});
}});

// ── Tab 2: 비교 테이블 + AI 브리핑 ─────────────────────
tsrInit(function () {{
  const cs = D.curated_specs || [];
  const meta = D.curated_meta || {{}};

  if (!cs.length) {{
    if ($('curatedSummary')) $('curatedSummary').innerHTML = '⚠ user-curated specs JSON 없음. <code>data/raw/*_specs_user_curated_*.json</code> 추가 후 dashboard 재생성.';
    return;
  }}

  const schemaVersion = meta.schema_version || '1.0';
  const companyFilters = (meta.company_filters || []);
  const totalMissions = cs.reduce((sum, s) => sum + ((s.notable_missions || []).length), 0);
  $('curatedSummary').innerHTML = `
    <b>${{cs.length}}개 로켓</b> · ${{totalMissions}}개 notable missions · ${{companyFilters.length}}개 company filters · schema v${{schemaVersion}} · 전체 신뢰도: ${{meta.reliability_overall||'?'}}
  `;

  // 회사 정렬 우선순위: SpaceX → Blue Origin → Rocket Lab → 그 외 알파벳순
  const COMPANY_PRIORITY = {{
    'spacex': 1,
    'blue origin': 2,
    'rocket lab': 3,
  }};
  function companyRank(name) {{
    const lower = String(name || '').toLowerCase();
    for (const k of Object.keys(COMPANY_PRIORITY)) {{
      if (lower.includes(k)) return COMPANY_PRIORITY[k];
    }}
    return 100; // 그 외는 알파벳 순으로 뒤에 정렬
  }}

  // v2: rocket_specs[]는 로켓만 담음. 미션은 각 로켓의 notable_missions[]로 흡수됨.
  const rows = cs.map((s, idx) => {{
    const company = s.manufacturer || '—';
    const ticker = s.ticker || '—';
    const country = s.country || '—';
    const model = s.model || '—';
    const title = s.model || '—';
    const heightM = s.height_m || s.combined_height_m || s.height_m_plus || null;
    let leoKg = null;
    if (typeof s.leo_capacity_kg === 'number') leoKg = s.leo_capacity_kg;
    else if (s.leo_capacity_kg && typeof s.leo_capacity_kg === 'object') {{
      leoKg = s.leo_capacity_kg.expended || s.leo_capacity_kg.downrange_landing || s.leo_capacity_kg.rtls_landing || null;
    }} else if (typeof s.leo_capacity_kg_reusable === 'number') leoKg = s.leo_capacity_kg_reusable;
    else if (typeof s.leo_capacity_kg_expended === 'number') leoKg = s.leo_capacity_kg_expended;
    else if (typeof s.leo_capacity_t === 'number') leoKg = s.leo_capacity_t * 1000;
    const gtoKg = s.gto_capacity_kg || (s.gto_capacity_t_plus ? s.gto_capacity_t_plus * 1000 : null);
    const reusable = (s.reusable === true ? 'full' : (s.reusable === false ? 'no' : (s.reusable || '—')));
    const firstLaunch = s.first_launch || s.first_orbital_launch || s.first_flight || s.first_flight_v3 || '—';
    const status2026 = s.operational_status_2026 || (s.cumulative_launches_2026_04 ? `누적 ${{s.cumulative_launches_2026_04}}회` : (s.success_rate_pct != null ? `성공률 ${{s.success_rate_pct}}%` : '운용 중'));
    const costPerKg = s.launch_cost_usd_per_kg_LEO_estimated || null;
    const successRate = s.success_rate_pct != null ? s.success_rate_pct : null;
    const stages = s.stages || '—';
    const engines = s.engines_first_stage || s.engines_super_heavy || s.engine || s.engines || '—';
    const missionCount = (s.notable_missions || []).length;
    return {{
      idx, company, ticker, country, title, model,
      companyRank: companyRank(company),
      height: heightM, leo: leoKg, gto: gtoKg,
      reusable, firstLaunch, status2026, costPerKg, successRate,
      stages, engines, nsfUrl: s.nsf_url || '',
      missionCount,
    }};
  }});

  // 사전 정렬: SpaceX → Blue Origin → Rocket Lab → 그 외(알파벳), 회사 내부에서는 LEO 적재 desc
  rows.sort((a, b) => {{
    if (a.companyRank !== b.companyRank) return a.companyRank - b.companyRank;
    if (a.companyRank === 100) {{
      const c = a.company.localeCompare(b.company);
      if (c !== 0) return c;
    }} else {{
      // 동일 우선순위 회사 내에서는 회사 이름 정확히 일치하므로 이어서 LEO desc
    }}
    return (b.leo || 0) - (a.leo || 0);
  }});

  new Tabulator('#curatedTable', {{
    data: rows,
    layout: 'fitDataStretch', height: 480, theme: 'midnight',
    initialSort: [{{column: 'companyRank', dir: 'asc'}}, {{column: 'leo', dir: 'desc'}}],
    columns: [
      {{title: '', field: 'companyRank', visible: false}},
      {{title: '회사', field: 'company', width: 170, headerFilter: 'input',
        sorter: (a, b, aRow, bRow) => {{
          const ra = aRow.getData().companyRank, rb = bRow.getData().companyRank;
          if (ra !== rb) return ra - rb;
          return String(a||'').localeCompare(String(b||''));
        }}}},
      {{title: '티커', field: 'ticker', width: 90, headerFilter: 'input'}},
      {{title: '국가', field: 'country', width: 80, headerFilter: 'list', headerFilterParams: {{valuesLookup: true, clearable: true}}}},
      {{title: '로켓 모델 / 미션명', field: 'title', width: 240, headerFilter: 'input', sorter: 'string',
        formatter: (cell) => `<b>${{cell.getValue()}}</b>`}},
      {{title: '단계', field: 'stages', width: 65, hozAlign: 'center'}},
      {{title: '높이 (m)', field: 'height', width: 95, hozAlign: 'right', sorter: 'number',
        formatter: (cell) => cell.getValue() != null ? cell.getValue().toFixed(1) : '—'}},
      {{title: 'LEO 적재 (kg)', field: 'leo', width: 130, hozAlign: 'right', sorter: 'number',
        formatter: (cell) => cell.getValue() != null ? cell.getValue().toLocaleString() : '—'}},
      {{title: 'GTO (kg)', field: 'gto', width: 100, hozAlign: 'right', sorter: 'number',
        formatter: (cell) => cell.getValue() != null ? cell.getValue().toLocaleString() : '—'}},
      {{title: '재사용', field: 'reusable', width: 110, headerFilter: 'input',
        formatter: (cell) => {{
          const v = String(cell.getValue() || '').toLowerCase();
          if (v.startsWith('full')) return '<span class="badge green">full</span>';
          if (v.startsWith('partial')) return '<span class="badge amber">partial</span>';
          if (v === 'no' || v === 'false') return '<span class="badge gray">no</span>';
          return v;
        }}}},
      {{title: '$/kg LEO', field: 'costPerKg', width: 110, hozAlign: 'right', sorter: 'number',
        formatter: (cell) => cell.getValue() ? '$' + cell.getValue().toLocaleString() : '—'}},
      {{title: '성공률', field: 'successRate', width: 90, hozAlign: 'right',
        formatter: (cell) => cell.getValue() != null ? cell.getValue() + '%' : '—'}},
      {{title: '엔진', field: 'engines', width: 200, formatter: (cell) => `<span style="font-size:11px">${{cell.getValue()||'—'}}</span>`}},
      {{title: '미션', field: 'missionCount', width: 70, hozAlign: 'center', sorter: 'number',
        formatter: (cell) => {{
          const n = cell.getValue() || 0;
          if (!n) return '<span class="muted" style="font-size:11px">—</span>';
          return `<span class="badge" style="background:#3a5fcd">${{n}}</span>`;
        }}}},
      {{title: '첫 발사 / 일자', field: 'firstLaunch', width: 160, headerFilter: 'input',
        formatter: (cell) => `<span style="font-size:11px">${{cell.getValue()||'—'}}</span>`}},
      {{title: '현재 상태', field: 'status2026', width: 200, headerFilter: 'input',
        formatter: (cell) => {{
          const v = cell.getValue() || '';
          let color = 'var(--text)';
          if (v.includes('PAUSED') || v.includes('off-nominal') || v.includes('실패')) color = '#ff6b6b';
          else if (v.includes('✅') || v.toLowerCase().includes('success')) color = '#7ee787';
          else if (v.includes('NET') || v.includes('예정')) color = '#a5d6ff';
          return `<span style="font-size:11px;color:${{color}}">${{v}}</span>`;
        }}}},
    ],
    rowClick: (e, row) => {{
      const idx = row.getData().idx;
      const target = document.getElementById('briefing-' + idx);
      if (target) {{
        document.querySelectorAll('details.briefing').forEach(d => d.removeAttribute('open'));
        target.setAttribute('open', 'open');
        target.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        target.style.boxShadow = '0 0 0 2px var(--accent)';
        setTimeout(() => {{ target.style.boxShadow = ''; }}, 1500);
      }}
    }},
  }});

  // 섹션별 신뢰도 뱃지 색상
  const RELIAB_COLOR = (tag) => {{
    const t = String(tag || '').toLowerCase();
    if (t.includes('1차')) return {{bg:'#1f6f3a', label:tag}};
    if (t.includes('1.5')) return {{bg:'#0c447c', label:tag}};
    if (t.includes('2차')) return {{bg:'#8a6d20', label:tag}};
    if (t.includes('3차')) return {{bg:'#5a2b2b', label:tag}};
    if (t.includes('n/a')) return {{bg:'#444', label:tag}};
    return {{bg:'#444', label:tag||'?'}};
  }};
  const SECTION_LABEL = {{
    physical_specs: '물리 사양',
    performance: '성능',
    mission_history: '발사 이력',
    ai_briefing: 'AI 브리핑',
  }};
  function renderSectionReliability(rps) {{
    if (!rps) return '';
    return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">` +
      Object.entries(rps).map(([k, v]) => {{
        const c = RELIAB_COLOR(v);
        return `<span class="badge" style="background:${{c.bg}};font-size:10px" title="${{k}}: ${{v}}">${{SECTION_LABEL[k]||k}} <b>${{c.label}}</b></span>`;
      }}).join('') +
    `</div>`;
  }}

  function renderMiniMission(m, parentIdx, mIdx) {{
    const ai = m.ai_briefing || {{}};
    const title = m.mission || m.name || '—';
    const dateStr = m.launch_date || m.date || m.launch_target_date || m.target_date || '';
    const customer = m.customer || '';
    const outcome = m.outcome || '';
    const axes = (m.axes || []).map(a => `<span class="badge" style="background:#444;font-size:9px">${{a}}</span>`).join(' ');
    const sources = (m.sources || []).map(src => `<li><code style="font-size:10px">${{src}}</code></li>`).join('');
    return `
      <details class="briefing-mission" style="background:#070b12;border:1px solid #1c2533;border-radius:8px;padding:8px 12px;margin-top:6px">
        <summary style="cursor:pointer;font-size:12px">
          <b>📍 ${{title}}</b>
          ${{dateStr ? `<span class="muted"> · ${{dateStr}}</span>` : ''}}
          ${{outcome ? `<span style="color:${{outcome.toLowerCase().includes('success') ? '#7ee787' : '#ffa657'}};font-size:11px"> · ${{outcome}}</span>` : ''}}
        </summary>
        <div style="margin-top:8px;font-size:12px;line-height:1.55">
          ${{customer ? `<div class="muted" style="font-size:11px">고객: ${{customer}}</div>` : ''}}
          ${{axes ? `<div style="margin:4px 0">${{axes}}</div>` : ''}}
          ${{ai.what_is_it ? `<div style="margin-top:6px">${{ai.what_is_it}}</div>` : ''}}
          ${{ai.significance ? `<div class="muted" style="margin-top:4px;font-size:11px"><b>의의:</b> ${{ai.significance}}</div>` : ''}}
          ${{m.reliability ? `<div style="margin-top:6px"><span class="badge" style="background:${{RELIAB_COLOR(m.reliability).bg}};font-size:10px">${{m.reliability}}</span></div>` : ''}}
          ${{sources ? `<details style="margin-top:6px;font-size:10px"><summary class="muted" style="cursor:pointer">🔗 source</summary><ul style="margin:4px 0 0 18px">${{sources}}</ul></details>` : ''}}
        </div>
      </details>`;
  }}

  // AI 브리핑 카드 — 회사 우선순위 + LEO desc 정렬 (rocket-only)
  const briefingItems = cs.map((s, idx) => ({{idx, raw: s, rank: companyRank(s.manufacturer || '')}}));
  briefingItems.sort((a, b) => {{
    if (a.rank !== b.rank) return a.rank - b.rank;
    if (a.rank === 100) {{
      const c = (a.raw.manufacturer || '').localeCompare(b.raw.manufacturer || '');
      if (c !== 0) return c;
    }}
    const getLeo = (r) => {{
      if (typeof r.leo_capacity_kg === 'number') return r.leo_capacity_kg;
      if (r.leo_capacity_kg && r.leo_capacity_kg.expended) return r.leo_capacity_kg.expended;
      return r.leo_capacity_kg_reusable || r.leo_capacity_kg_expended || (r.leo_capacity_t ? r.leo_capacity_t * 1000 : 0);
    }};
    return getLeo(b.raw) - getLeo(a.raw);
  }});

  $('curatedBriefings').innerHTML = briefingItems.map(({{idx, raw: s}}) => {{
    const ai = s.ai_briefing || {{}};
    const title = s.model || '—';
    const subtitle = s.manufacturer || '';
    const sources = (s.sources || []).map(src => `<li><code style="font-size:10px">${{src}}</code></li>`).join('');
    const reliabRows = s.reliability_per_field
      ? Object.entries(s.reliability_per_field).map(([k, v]) => `<div class="row"><span style="font-size:11px">${{k}}</span><b style="font-size:10px">${{v}}</b></div>`).join('')
      : '';
    const sectionBadges = renderSectionReliability(s.reliability_per_section);
    const missions = s.notable_missions || [];
    const missionsBlock = missions.length
      ? `<div style="margin-top:14px;border-top:1px dashed var(--line);padding-top:10px">
          <b style="color:#a5d6ff">📍 Notable Missions (${{missions.length}})</b>
          ${{missions.map((m, i) => renderMiniMission(m, idx, i)).join('')}}
        </div>`
      : '';

    return `
      <details id="briefing-${{idx}}" class="briefing" style="background:#0b1018;border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:10px;transition:box-shadow 0.3s">
        <summary style="cursor:pointer;font-size:14px;font-weight:600">
          ${{title}}
          ${{subtitle ? `<span class="muted" style="font-weight:400;font-size:12px"> — ${{subtitle}}</span>` : ''}}
          ${{missions.length ? `<span class="badge" style="background:#3a5fcd;font-size:10px;margin-left:6px">${{missions.length}} missions</span>` : ''}}
        </summary>
        ${{sectionBadges}}
        <div style="margin-top:14px;display:grid;gap:10px;font-size:13px;line-height:1.6">
          ${{ai.what_is_it ? `<div><b style="color:var(--accent)">🚀 개요</b><br>${{ai.what_is_it}}</div>` : ''}}
          ${{ai.mission_purpose ? `<div><b style="color:#7ee787">🎯 미션 / 목적</b><br>${{ai.mission_purpose}}</div>` : ''}}
          ${{ai.current_status || ai.current_status_2026_04 ? `<div><b style="color:#ffa657">📍 현재 상황</b><br>${{ai.current_status || ai.current_status_2026_04}}</div>` : ''}}
          ${{ai.significance ? `<div><b style="color:#a5d6ff">💡 의의</b><br>${{ai.significance}}</div>` : ''}}
        </div>
        ${{missionsBlock}}
        <div style="margin-top:14px;display:flex;gap:14px;flex-wrap:wrap">
          ${{s.nsf_url ? `<a href="${{s.nsf_url}}" target="_blank" style="color:var(--accent);font-size:11px">🔗 ${{s.nsf_url.replace('https://','').slice(0,60)}}</a>` : ''}}
          ${{reliabRows ? `<details style="font-size:11px"><summary style="cursor:pointer;color:var(--muted)">📌 항목별 신뢰도</summary><div style="margin-top:6px">${{reliabRows}}</div></details>` : ''}}
          <details style="font-size:11px"><summary style="cursor:pointer;color:var(--muted)">🔗 인용 source</summary><ul style="margin:6px 0 0 18px">${{sources}}</ul></details>
        </div>
      </details>
    `;
  }}).join('');

  // Company filters (예: Blue Origin agency_39) — 별도 섹션
  if (companyFilters.length) {{
    const filtersHtml = companyFilters.map((f, i) => {{
      const ai = f.ai_briefing || {{}};
      const upcoming = (f.key_upcoming_2026 || []).map(u => `
        <li style="font-size:11px">
          <b>${{u.mission}}</b> · ${{u.rocket||''}} · ${{u.target_date||''}}
          ${{u.outcome ? `<span class="muted"> — ${{u.outcome}}</span>` : ''}}
          ${{u.reliability ? `<span class="badge" style="background:${{RELIAB_COLOR(u.reliability).bg}};font-size:9px;margin-left:4px">${{u.reliability}}</span>` : ''}}
        </li>`).join('');
      const sources = (f.sources || []).map(src => `<li><code style="font-size:10px">${{src}}</code></li>`).join('');
      return `
        <details class="briefing" style="background:#0b1018;border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:10px">
          <summary style="cursor:pointer;font-size:13px;font-weight:600">
            🏷️ ${{f.agency || 'company'}} 필터 · <span class="muted" style="font-weight:400;font-size:11px">${{f.filter_meaning||''}}</span>
          </summary>
          <div style="margin-top:12px;font-size:13px;line-height:1.6">
            ${{ai.current_status_2026_04 ? `<div><b style="color:#ffa657">📍 현재 상황</b><br>${{ai.current_status_2026_04}}</div>` : ''}}
            ${{ai.significance ? `<div style="margin-top:8px"><b style="color:#a5d6ff">💡 의의</b><br>${{ai.significance}}</div>` : ''}}
            ${{f.operational_paused ? `<div class="muted" style="margin-top:8px;font-size:11px">⚠ ${{f.operational_paused}}</div>` : ''}}
            ${{upcoming ? `<div style="margin-top:10px"><b>예정 미션</b><ul style="margin:4px 0 0 16px">${{upcoming}}</ul></div>` : ''}}
            ${{sources ? `<details style="font-size:11px;margin-top:8px"><summary style="cursor:pointer;color:var(--muted)">🔗 source</summary><ul style="margin:6px 0 0 18px">${{sources}}</ul></details>` : ''}}
          </div>
        </details>`;
    }}).join('');
    const wrap = document.createElement('div');
    wrap.className = 'card';
    wrap.style.marginTop = '16px';
    wrap.innerHTML = `<h2>🏷️ Company Filters</h2><p class="muted" style="margin-bottom:8px">회사 단위 매니페스트 (NSF agency 필터)</p>${{filtersHtml}}`;
    const briefingsCard = $('curatedBriefings').closest('.card');
    if (briefingsCard && briefingsCard.parentNode) briefingsCard.parentNode.insertBefore(wrap, briefingsCard.nextSibling);
  }}
}});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())

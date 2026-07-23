#!/usr/bin/env python3
"""
analyze_rocket_data.py — Phase 2 (analyst 역할)

data/raw/ 의 LL2 수집물을 받아 4축 매트릭스 + 투자 시사점으로 정렬한
analysis/{YYYYMMDD}_rocket_analysis.md 를 생성한다.
(AGENTS.md frontmatter 포맷 준수)

산출 부산물:
  - data/raw/{YYYYMMDD}_analysis_TSR_rocket_metrics.json
    (대시보드 Phase 3 가 직접 읽는 정량 메트릭 캐시)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
ANALYSIS_DIR = ROOT / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")
TODAY_DASHED = datetime.now(timezone.utc).strftime("%Y-%m-%d")
NOW_ISO = datetime.now(timezone.utc).isoformat()

AXIS_LABEL_KO = {
    "territory": "영토&자원",
    "comms": "통신",
    "energy_logistics": "에너지&물류",
    "defense": "군사&안보",
}

# 기업 색상 (대시보드와 공유)
COMPANY_COLORS = {
    "SpaceX": "#005288",
    "Rocket Lab": "#FF3B30",
    "Rocket Lab Ltd": "#FF3B30",
    "Blue Origin": "#1A4B8C",
}


# ----------------------------------------------------------------------------
# I/O
# ----------------------------------------------------------------------------
def latest_file(pattern: str) -> Path | None:
    """오늘 날짜 기준 우선, 없으면 가장 최근 파일."""
    today_match = sorted(RAW_DIR.glob(pattern.replace("*", TODAY, 1)))
    if today_match:
        return today_match[-1]
    files = sorted(RAW_DIR.glob(pattern))
    return files[-1] if files else None


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# 메트릭
# ----------------------------------------------------------------------------
def yoy_by_company(history_files: dict[str, Path]) -> dict:
    """기업별 연도별 발사 횟수 + YoY 성장률."""
    by_company: dict[str, Counter] = defaultdict(Counter)
    for slug, path in history_files.items():
        if not path:
            continue
        data = load(path)
        for r in data["records"]:
            net = r.get("net")
            if not net:
                continue
            year = net[:4]
            by_company[slug][year] += 1

    series = {}
    for slug, ctr in by_company.items():
        years = sorted(ctr.keys())
        rows = []
        prev = None
        for y in years:
            count = ctr[y]
            yoy = None if prev in (None, 0) else round((count - prev) / prev * 100, 1)
            rows.append({"year": y, "count": count, "yoy_pct": yoy})
            prev = count
        series[slug] = rows
    return series


def success_rate(history_files: dict[str, Path]) -> dict:
    """기업별/로켓모델별 성공률."""
    by_company = defaultdict(lambda: Counter())
    by_model = defaultdict(lambda: Counter())

    for slug, path in history_files.items():
        if not path:
            continue
        for r in load(path)["records"]:
            res = r.get("result")
            if not res:
                continue
            by_company[slug][res] += 1
            model = r.get("rocket_model") or "Unknown"
            by_model[(slug, model)][res] += 1

    def _rate(c: Counter) -> dict:
        total = sum(c.values())
        if total == 0:
            return {"total": 0, "success": 0, "failure": 0, "partial": 0, "rate_pct": None}
        return {
            "total": total,
            "success": c.get("success", 0),
            "failure": c.get("failure", 0),
            "partial": c.get("partial", 0),
            "rate_pct": round(c.get("success", 0) / total * 100, 1),
        }

    return {
        "by_company": {k: _rate(v) for k, v in by_company.items()},
        "by_model": [
            {"company": k[0], "model": k[1], **_rate(v)}
            for k, v in sorted(by_model.items(), key=lambda kv: -sum(kv[1].values()))
        ],
    }


def market_share(history_files: dict[str, Path], year_floor: int = 2020) -> dict:
    """발사 횟수 + 페이로드 기반 점유율 (LL2 는 페이로드 kg 미제공 → 횟수 위주)."""
    counts: Counter = Counter()
    by_year_company: dict[str, Counter] = defaultdict(Counter)
    for slug, path in history_files.items():
        if not path:
            continue
        for r in load(path)["records"]:
            net = r.get("net") or ""
            if not net or int(net[:4] or 0) < year_floor:
                continue
            counts[slug] += 1
            by_year_company[net[:4]][slug] += 1

    total = sum(counts.values()) or 1
    share = {k: round(v / total * 100, 1) for k, v in counts.items()}
    return {
        "since_year": year_floor,
        "total_launches": total,
        "share_pct": share,
        "raw_counts": dict(counts),
        "by_year": {y: dict(c) for y, c in sorted(by_year_company.items())},
    }


def cost_per_kg(specs_path: Path | None) -> list[dict]:
    """$/kg-to-LEO 추정. LEO 적재량과 launch_cost 가 모두 있는 모델만."""
    if not specs_path:
        return []
    rows = []
    for cfg in load(specs_path)["records"]:
        cost = cfg.get("launch_cost_usd")
        leo = cfg.get("leo_capacity_kg")
        if not cost or not leo or leo <= 0:
            continue
        rows.append({
            "model": cfg.get("full_name") or cfg.get("name"),
            "manufacturer": cfg.get("manufacturer"),
            "leo_capacity_kg": leo,
            "launch_cost_usd": cost,
            "usd_per_kg_leo": round(cost / leo, 0),
            "reusable": cfg.get("reusable"),
        })
    rows.sort(key=lambda x: x["usd_per_kg_leo"])
    return rows


def axis_distribution(history_files: dict[str, Path], upcoming_path: Path | None) -> dict:
    """4축 미션 분포 — 과거 + 예정 합산, 기업별·연도별 분리."""
    overall: Counter = Counter()
    by_company: dict[str, Counter] = defaultdict(Counter)
    by_year: dict[str, Counter] = defaultdict(Counter)

    sources = list(history_files.values())
    if upcoming_path:
        sources.append(upcoming_path)

    for path in sources:
        if not path:
            continue
        for r in load(path)["records"]:
            axes = r.get("axes") or []
            company = r.get("company") or "Unknown"
            year = (r.get("net") or "")[:4] or "TBD"
            for a in axes:
                overall[a] += 1
                by_company[company][a] += 1
                by_year[year][a] += 1

    return {
        "overall": dict(overall),
        "by_company": {k: dict(v) for k, v in by_company.items()},
        "by_year": {y: dict(c) for y, c in sorted(by_year.items())},
    }


def commercial_highlights(history_files: dict[str, Path], upcoming_path: Path | None) -> list[dict]:
    """RKLB / 상업 미션 주목 리스트 (예정 + 최근 12개월 history)."""
    highlights: list[dict] = []

    rocketlab_path = history_files.get("rocketlab")
    if rocketlab_path:
        records = load(rocketlab_path)["records"]
        recent = sorted(
            (r for r in records if r.get("net")),
            key=lambda r: r["net"],
            reverse=True,
        )[:5]
        for r in recent:
            highlights.append({
                "kind": "RKLB recent",
                "date": (r.get("net") or "")[:10],
                "rocket": r.get("rocket_model"),
                "mission": r.get("mission_name"),
                "result": r.get("result"),
                "axes": r.get("axes"),
            })

    if upcoming_path:
        upcoming = load(upcoming_path)["records"]
        for r in upcoming[:10]:
            company = (r.get("company") or "")
            if "rocket lab" in company.lower():
                highlights.append({
                    "kind": "RKLB upcoming",
                    "date": (r.get("net") or "")[:10],
                    "rocket": r.get("rocket_model"),
                    "mission": r.get("mission_name"),
                    "axes": r.get("axes"),
                })
    return highlights


# ----------------------------------------------------------------------------
# 마크다운 빌더 (AGENTS.md 포맷)
# ----------------------------------------------------------------------------
def render_markdown(metrics: dict, raw_files: dict) -> str:
    yoy = metrics["yoy"]
    rate = metrics["success_rate"]
    share = metrics["market_share"]
    cpk = metrics["cost_per_kg"]
    axis = metrics["axis_distribution"]
    hi = metrics["highlights"]

    md: list[str] = []

    # frontmatter — AGENTS.md 호환
    md.append("---")
    md.append("topic: rocket")
    md.append(f"date: {TODAY_DASHED}")
    md.append("stage: analysis")
    md.append("axes: [territory, comms, energy_logistics, defense]")
    md.append("upstream: data/raw/" + raw_files["upcoming"].name if raw_files.get("upcoming") else "upstream: data/raw/")
    md.append("---")
    md.append("")
    md.append("# Rocket Launch — Strategic Analysis")
    md.append("")

    # TL;DR
    total_launches = share.get("total_launches", 0)
    top_share = max(share["share_pct"].items(), key=lambda kv: kv[1]) if share.get("share_pct") else ("?", 0)
    cheapest = cpk[0] if cpk else None
    md.append("## TL;DR (3줄)")
    md.append(f"1. {share.get('since_year','?')}년 이후 3사 누적 {total_launches}회 발사 — 점유율 1위 **{top_share[0]} {top_share[1]}%**.")
    if cheapest:
        md.append(f"2. $/kg-to-LEO 최저 모델: **{cheapest['model']}** = ${cheapest['usd_per_kg_leo']:,}/kg ({cheapest['manufacturer']}, 재사용={cheapest['reusable']}).")
    else:
        md.append("2. $/kg-to-LEO 비교: 데이터 부족 (LL2 specs 의 launch_cost 비공개 모델 다수).")
    md.append(f"3. 4축 분포 — comms 위성 비중 {axis['overall'].get('comms',0)}건 vs defense {axis['overall'].get('defense',0)}건 (territory {axis['overall'].get('territory',0)} / energy_logistics {axis['overall'].get('energy_logistics',0)}).")
    md.append("")

    # 4축 매트릭스
    md.append("## 4축 매트릭스")
    md.append("")
    md.append("| 축 | 밸류체인 | 경쟁강도 | 수익화 | 패권변수 | 수혜 | 피해 |")
    md.append("|---|---|---|---|---|---|---|")
    md.append("| 영토&자원 | 발사 (운송) → 행성 거점 | 중 (NASA CLPS·HLS 의존) | 5y+ | NASA 예산·달 남극 거점 | LMT, NOC, RKLB(소형 lander) | 발사 capacity 부족社 |")
    md.append("| 통신 | 발사 → 컨스텔레이션 배치 | 강 (SpaceX 독주) | 0~2y | 발사 cadence + ITU 슬롯 | SpaceX(미상장), ASTS, GSAT | 단일 발사 의존社 |")
    md.append("| 에너지&물류 | 본 축 — 재사용 로켓 자체 | 강 (가격 압박) | 0~2y | $/kg, 재사용 횟수, 발사 cadence | SpaceX, RKLB(Neutron) | ULA, Arianespace, BO(현재) |")
    md.append("| 군사&안보 | 발사 → SDA Tranche / NRO | 중 (인증 진입장벽) | 2~5y | NSSL Phase 3 라인업, USSF 예산 | SpaceX, ULA, RKLB(인증중) | 인증 미보유社 |")
    md.append("")

    # 발사 빈도 추이
    md.append("## 1. 발사 빈도 추이 (YoY)")
    md.append("")
    for slug, rows in yoy.items():
        if not rows:
            continue
        md.append(f"### {slug}")
        md.append("| 연도 | 발사수 | YoY |")
        md.append("|---|---|---|")
        for r in rows[-8:]:
            yoy_str = "—" if r["yoy_pct"] is None else f"{r['yoy_pct']:+.1f}%"
            md.append(f"| {r['year']} | {r['count']} | {yoy_str} |")
        md.append("")

    # 성공률
    md.append("## 2. 성공률 비교")
    md.append("")
    md.append("### 기업별")
    md.append("| 기업 | 총 | 성공 | 실패 | 부분 | 성공률 |")
    md.append("|---|---|---|---|---|---|")
    for slug, s in rate["by_company"].items():
        md.append(f"| {slug} | {s['total']} | {s['success']} | {s['failure']} | {s['partial']} | {s['rate_pct']}% |")
    md.append("")
    md.append("### 로켓 모델별 (상위 10)")
    md.append("| 기업 | 모델 | 총 | 성공 | 성공률 |")
    md.append("|---|---|---|---|---|")
    for r in rate["by_model"][:10]:
        md.append(f"| {r['company']} | {r['model']} | {r['total']} | {r['success']} | {r['rate_pct']}% |")
    md.append("")

    # 시장 점유율
    md.append("## 3. 시장 점유율 (발사 횟수 기준)")
    md.append(f"_기준: {share['since_year']}년 이후, 누적 {share['total_launches']}회_")
    md.append("")
    md.append("| 기업 | 발사수 | 점유율 |")
    md.append("|---|---|---|")
    for slug, pct in sorted(share["share_pct"].items(), key=lambda kv: -kv[1]):
        md.append(f"| {slug} | {share['raw_counts'].get(slug,0)} | {pct}% |")
    md.append("")
    md.append("> ⚠ 페이로드 kg 기준 점유율은 LL2 미공개 — 추후 SpaceX/RKLB 자체 공시로 보강 필요 [⚠ 추정].")
    md.append("")

    # 발사 단가
    md.append("## 4. 발사 단가 추정 ($/kg-to-LEO)")
    md.append("")
    md.append("| 모델 | 제조사 | LEO kg | 발사비 (USD) | $/kg | 재사용 |")
    md.append("|---|---|---|---|---|---|")
    if cpk:
        for r in cpk[:15]:
            md.append(f"| {r['model']} | {r['manufacturer']} | {r['leo_capacity_kg']:,} | ${r['launch_cost_usd']:,} | ${r['usd_per_kg_leo']:,} | {'✅' if r['reusable'] else '—'} |")
    else:
        md.append("| — | — | — | — | — | — |")
    md.append("")

    # 4축 분포
    md.append("## 5. 4축 분포 (미션 목적별)")
    md.append("| 축 | 건수 |")
    md.append("|---|---|")
    for k, label in AXIS_LABEL_KO.items():
        md.append(f"| {label} | {axis['overall'].get(k, 0)} |")
    md.append("")

    # 투자 시사점
    md.append("## 6. 주식 관련 시사점")
    md.append("")
    md.append("### 수혜 후보")
    md.append("- **RKLB** — 가설: Neutron 데뷔로 medium-lift 진입, defense (SDA·NSSL) 인증 확장.  ")
    md.append("  트리거: Neutron 첫 발사 성공 + USSF NSSL Phase 3 Lane 1 수주.  ")
    md.append("  반증조건: Neutron 일정 6개월 이상 추가 슬립 / 첫 발사 실패.")
    md.append("- **ASTS** — 가설: D2C 컨스텔레이션 배치는 발사 cadence 종속 → SpaceX/ULA 발사 슬롯 확보 시 NPV 기울기 가팔라짐.  ")
    md.append("  트리거: Bluebird block 1 5기 정상 운용 + 통신사 매출 인식.  ")
    md.append("  반증조건: 펀딩 갭 / FCC STA 갱신 거부.")
    md.append("")
    md.append("### 피해 후보 / 관전")
    md.append("- **ULA (BA·LMT 50/50)** — Vulcan cadence 가 SpaceX Falcon 대비 분기당 1/5 수준 유지 시 NSSL 점유 추가 잠식.")
    md.append("- **Blue Origin** — 비상장. New Glenn 의 reusable booster 회수 성공 빈도가 결정 변수. ⚠ 추정.")
    md.append("")
    md.append("### Watchlist 이벤트")
    if hi:
        for h in hi[:8]:
            line = f"- {h.get('date','TBD')} · {h.get('kind')} · {h.get('rocket') or '—'} · {h.get('mission') or '—'}"
            if h.get("axes"):
                line += f"  [{', '.join(h['axes'])}]"
            md.append(line)
    md.append("")

    # 데이터 출처
    md.append("## 데이터 출처")
    for k, p in raw_files.items():
        if p:
            md.append(f"- [1.5차] `data/raw/{p.name}`")
    md.append("")
    md.append(f"_생성: {NOW_ISO}_")
    md.append("")

    return "\n".join(md)


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main() -> int:
    print(f"📊 analyze_rocket_data.py  ({NOW_ISO})")

    raw_files = {
        "upcoming": latest_file(f"*_upcoming_launchlibrary2_launches.json"),
        "spacex": latest_file(f"*_history_spacex_launches.json"),
        "rocketlab": latest_file(f"*_history_rocketlab_launches.json"),
        "blueorigin": latest_file(f"*_history_blueorigin_launches.json"),
        "firefly": latest_file(f"*_history_firefly_launches.json"),
        "specs": latest_file(f"*_specs_launchlibrary2_rockets.json"),
    }
    missing = [k for k, v in raw_files.items() if not v]
    if missing:
        print(f"❌ 입력 누락: {missing} — fetch_rocket_data.py 를 먼저 실행하세요.", file=sys.stderr)
        return 1

    history_files = {
        "spacex": raw_files["spacex"],
        "rocketlab": raw_files["rocketlab"],
        "blueorigin": raw_files["blueorigin"],
        "firefly": raw_files["firefly"],
    }

    metrics = {
        "yoy": yoy_by_company(history_files),
        "success_rate": success_rate(history_files),
        "market_share": market_share(history_files),
        "cost_per_kg": cost_per_kg(raw_files["specs"]),
        "axis_distribution": axis_distribution(history_files, raw_files["upcoming"]),
        "highlights": commercial_highlights(history_files, raw_files["upcoming"]),
    }

    # 1) 정량 메트릭 캐시 (대시보드 입력)
    metrics_path = RAW_DIR / f"{TODAY}_analysis_TSR_rocket_metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "fetched_at": NOW_ISO,
                "source": "TSR analyst (analyze_rocket_data.py)",
                "reliability": "1.5차",
                "axis": ["territory", "comms", "energy_logistics", "defense"],
            },
            "metrics": metrics,
            "raw_files": {k: (str(v.relative_to(ROOT)) if v else None) for k, v in raw_files.items()},
        }, f, ensure_ascii=False, indent=2)

    # 2) 마크다운 (AGENTS.md frontmatter 포맷)
    md = render_markdown(metrics, raw_files)
    md_path = ANALYSIS_DIR / f"{TODAY}_rocket_analysis.md"
    md_path.write_text(md, encoding="utf-8")

    print("✅ 분석 완료:")
    print(f"   - {metrics_path.relative_to(ROOT)}")
    print(f"   - {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

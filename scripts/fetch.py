#!/usr/bin/env python3
"""
fetch_rocket_data.py — Phase 1 (researcher 역할)

CLAUDE.md/CORE_CONCEPT.md 4축 프레임워크에 정렬된 발사 데이터를
Launch Library 2 (1.5차) 에서 수집해서 data/raw/ 에 저장한다.

산출 (CLAUDE.md 파일명 규칙: YYYYMMDD_성격_출처_제목.json):
  - {date}_upcoming_launchlibrary2_launches.json    [1.5차]
  - {date}_history_spacex_launches.json             [1.5차]
  - {date}_history_rocketlab_launches.json          [1.5차]
  - {date}_history_blueorigin_launches.json         [1.5차]
  - {date}_specs_launchlibrary2_rockets.json        [1.5차]

각 파일에 메타: {fetched_at, source, reliability, axis}
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import urllib.request
import urllib.error

# ----------------------------------------------------------------------------
# 경로 / 상수
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

LL2_BASE = "https://ll.thespacedevs.com/2.2.0"
TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")
NOW_ISO = datetime.now(timezone.utc).isoformat()

# Launch Library 2 의 launch_service_provider id
AGENCY_IDS = {
    "spacex": 121,       # SpaceX
    "rocketlab": 147,    # Rocket Lab
    "blueorigin": 141,   # Blue Origin
    "firefly": 265,      # Firefly Aerospace
}

REQUEST_DELAY_SEC = 1.5  # rate limit 대비
PAGE_LIMIT = 100         # API 최대 limit
MAX_PAGES = 10           # 안전장치
HTTP_TIMEOUT = 45        # 30→45s. LL2 가 느릴 때가 많음
HTTP_RETRIES = 3         # 지수백오프 1s/2s/4s
HTTP_RETRY_BACKOFF = (1, 2, 4)  # 시도별 sleep 초


# ----------------------------------------------------------------------------
# 4축 태깅 (CORE_CONCEPT.md SSOT)
# ----------------------------------------------------------------------------
def tag_axes(mission_name: str, mission_type: str | None, orbit: str | None) -> list[str]:
    """미션 메타에서 4축 태그 추출. 복수 태깅 허용."""
    name = (mission_name or "").lower()
    mtype = (mission_type or "").lower()
    orb = (orbit or "").lower()
    text = f"{name} {mtype} {orb}"

    axes: set[str] = set()

    # ① 영토 & 자원 — 달/화성/심우주 미션
    if any(k in text for k in ["lunar", "moon", "mars", "deep space", "asteroid",
                                "artemis", "im-", "intuitive machines", "starship hls"]):
        axes.add("territory")

    # ② 통신 — 통신 위성 컨스텔레이션
    if any(k in text for k in ["starlink", "oneweb", "kuiper", "iridium", "globalstar",
                                "ses ", "eutelsat", "viasat", "communications", "comsat",
                                "bluebird", "ast spacemobile", "d2c", "telecom"]):
        axes.add("comms")

    # ④ 군사 & 안보 — 정찰/SDA/USSF/NRO
    if any(k in text for k in ["ussf", "usa-", "nrol", "sda ", "sda-", "tranche",
                                "missile defense", "reconnaissance", "spy",
                                "national security", "sbirs", "next-gen opir",
                                "gps ", "wgs ", "dod ", "sar ", "espa"]):
        axes.add("defense")

    # ③ 에너지 & 물류 — 운송 그 자체 (재사용/카고/예인선)
    if any(k in text for k in ["dragon", "cargo", "crs-", "cygnus", "iss",
                                "transporter", "rideshare", "bandwagon", "tug",
                                "orbital transfer", "in-space"]):
        axes.add("energy_logistics")

    # 폴백: 발사 자체는 "물류" — 어떤 축에도 안 잡히면 logistics 부여
    if not axes:
        axes.add("energy_logistics")

    # 안정적 정렬
    order = ["territory", "comms", "energy_logistics", "defense"]
    return [a for a in order if a in axes]


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def http_get(url: str, params: dict | None = None) -> dict:
    """LL2 API GET — retry/backoff 포함.
    - 일시 장애 (socket timeout, ConnectionResetError, 5xx, 429): 지수백오프로 최대 3회 재시도.
    - 429 면 Retry-After 헤더 존중 (단 5분 상한; 그 이상이면 retry 포기 → fallback 으로 넘김).
    - 4xx (429 외) 는 즉시 raise (fix 가능한 에러).
    """
    if params:
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "trading-space-research/1.0 (rocket research module)",
            "Accept": "application/json",
        },
    )
    last_err: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            # 429: Retry-After 존중 (5분 상한)
            if e.code == 429:
                ra_raw = e.headers.get("Retry-After") if hasattr(e, "headers") and e.headers else None
                ra = None
                try:
                    ra = int(ra_raw) if ra_raw else None
                except (TypeError, ValueError):
                    ra = None
                if ra is not None and ra > 300:
                    # 5분 초과 throttle 은 그날은 포기. fallback 흐름으로.
                    raise RuntimeError(f"HTTP 429 throttled {ra}s (>300s 상한) on {url}\n{body}") from e
                wait = ra if ra else HTTP_RETRY_BACKOFF[attempt] * 4
                print(f"  ⚠ 429 throttle — {wait}s 대기 후 재시도 ({attempt+1}/{HTTP_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                last_err = e
                continue
            # 5xx: 재시도 가치 있음
            if 500 <= e.code < 600 and attempt < HTTP_RETRIES - 1:
                wait = HTTP_RETRY_BACKOFF[attempt]
                print(f"  ⚠ HTTP {e.code} — {wait}s 후 재시도 ({attempt+1}/{HTTP_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                last_err = e
                continue
            # 그 외 4xx: 즉시 fail
            raise RuntimeError(f"HTTP {e.code} on {url}\n{body}") from e
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as e:
            # 일시 네트워크 오류: 지수백오프 재시도
            if attempt < HTTP_RETRIES - 1:
                wait = HTTP_RETRY_BACKOFF[attempt]
                reason = getattr(e, "reason", e)
                print(f"  ⚠ 네트워크 오류 ({reason}) — {wait}s 후 재시도 ({attempt+1}/{HTTP_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                last_err = e
                continue
            raise RuntimeError(f"네트워크 오류 (재시도 {HTTP_RETRIES}회 모두 실패) on {url}: {e}") from e
    # for 루프 빠지면 = 최종 시도 실패
    raise RuntimeError(f"HTTP 재시도 모두 소진 on {url}: {last_err}")


def fetch_paginated(endpoint: str, params: dict, max_pages: int = MAX_PAGES) -> list[dict]:
    """LL2 의 next 링크 따라가며 결과 누적."""
    results: list[dict] = []
    url: str | None = f"{LL2_BASE}{endpoint}"
    page = 0
    first = True
    while url and page < max_pages:
        data = http_get(url, params if first else None)
        results.extend(data.get("results", []))
        url = data.get("next")
        page += 1
        first = False
        if url:
            time.sleep(REQUEST_DELAY_SEC)
    return results


# ----------------------------------------------------------------------------
# 변환 — LL2 raw → 우리 스키마
# ----------------------------------------------------------------------------
def normalize_launch(launch: dict) -> dict:
    """LL2 launch 객체 → 표준화된 발사 레코드."""
    rocket = launch.get("rocket") or {}
    rconf = rocket.get("configuration") or {}
    pad = launch.get("pad") or {}
    location = pad.get("location") or {}
    lsp = launch.get("launch_service_provider") or {}
    mission = launch.get("mission") or {}
    status = launch.get("status") or {}
    orbit = (mission.get("orbit") or {}).get("name") if mission else None

    mission_name = mission.get("name") if mission else launch.get("name")
    mission_type = mission.get("type") if mission else None
    mission_desc = mission.get("description") if mission else None

    return {
        "id": launch.get("id"),
        "name": launch.get("name"),
        "net": launch.get("net"),                       # 발사일시 (ISO)
        "window_start": launch.get("window_start"),
        "window_end": launch.get("window_end"),
        "status": {
            "id": status.get("id"),
            "name": status.get("name"),
            "abbrev": status.get("abbrev"),
        },
        "company": lsp.get("name"),
        "company_country": lsp.get("country_code"),
        "rocket_model": rconf.get("name") or rconf.get("full_name"),
        "rocket_family": rconf.get("family"),
        "rocket_variant": rconf.get("variant"),
        "rocket_reusable": rconf.get("reusable"),
        "pad_name": pad.get("name"),
        "pad_location": location.get("name"),
        "pad_country": location.get("country_code"),
        "mission_name": mission_name,
        "mission_type": mission_type,
        "mission_description": mission_desc,
        "orbit": orbit,
        "customer": _extract_customer(launch, mission, mission_name, mission_desc, lsp),
        "image": launch.get("image"),
        "axes": tag_axes(mission_name, mission_type, orbit),
        "result": _result_from_status(status),
    }


# 미션명/설명 패턴 → 실제 고객명 매핑 (LL2 가 customer 필드 약하므로 휴리스틱 보조)
_CUSTOMER_PATTERNS = [
    (r"\bAST SpaceMobile\b|\bBlueBird\b", "AST SpaceMobile"),
    (r"\bStarlink\b", "SpaceX (Starlink)"),
    (r"\bKuiper\b", "Amazon (Project Kuiper)"),
    (r"\bDragon\b.*\bISS\b|\bCRS-\d+\b|\bCargo Dragon\b", "NASA (CRS)"),
    (r"\bCrew[- ]?\d+\b|\bAxiom\b", "NASA (Commercial Crew)"),
    (r"\bUSSF[- ]?\d+\b|\bNROL[- ]?\d+\b|\bSDA Tranche\b", "USSF/NRO"),
    (r"\bVICTUS\b", "USSF (Tactically Responsive Space)"),
    (r"\bTacSat\b", "USSF/SDA"),
    (r"\bINCUS\b", "NASA (Earth Venture)"),
    (r"\bQuickSounder\b", "NOAA"),
    (r"\bNASA\b", "NASA"),
    (r"\bGalileo\b", "EU (Galileo)"),
    (r"\bOneWeb\b", "OneWeb / Eutelsat"),
    (r"\bO3b\b|\bSES\b", "SES"),
    (r"\bEscaPADE\b", "NASA (EscaPADE)"),
    (r"\bBlue Moon\b|\bVIPER\b", "NASA (Artemis)"),
    (r"\bIM-\d+\b|\bIntuitive Machines\b", "Intuitive Machines / NASA CLPS"),
    (r"\bLightspeed\b|\bTelesat\b", "Telesat"),
    (r"\bJAXA\b|\bH3\b|\bHTV\b|\bKakushin\b", "JAXA"),
    (r"\bChandrayaan\b|\bGaganyaan\b|\bISRO\b", "ISRO"),
    (r"\bGalactic\b.*\b(JPSS|NPP)\b|\bJPSS\b", "NOAA"),
]


def _extract_customer(launch: dict, mission: dict | None, mname: str | None,
                      mdesc: str | None, lsp: dict) -> str:
    """LL2 mission.agencies (Customer 타입) 우선, 없으면 description 휴리스틱, 최종 LSP fallback."""
    import re
    # 1) mission.agencies 안에 type='Customer' 인 항목 찾기 (LL2 detailed mode)
    if mission:
        agencies = mission.get("agencies") or []
        for ag in agencies:
            t = (ag.get("type") or {})
            type_name = t.get("name") if isinstance(t, dict) else (t or "")
            if isinstance(type_name, str) and "customer" in type_name.lower():
                return ag.get("name") or ag.get("abbrev") or ""
    # 2) mission_name + mission_description 휴리스틱
    haystack = " ".join([mname or "", mdesc or ""])
    for pat, label in _CUSTOMER_PATTERNS:
        if re.search(pat, haystack, re.IGNORECASE):
            return label
    # 3) LSP fallback (위성 사업자 = 발사 제공자 = 자체 발사 케이스)
    return lsp.get("name") or "—"


def _result_from_status(status: dict) -> str | None:
    abbrev = (status or {}).get("abbrev") or ""
    name = (status or {}).get("name") or ""
    s = f"{abbrev} {name}".lower()
    if "success" in s and "partial" not in s:
        return "success"
    if "partial" in s:
        return "partial"
    if "failure" in s or "fail" in s:
        return "failure"
    return None


def normalize_rocket_config(cfg: dict) -> dict:
    return {
        "id": cfg.get("id"),
        "name": cfg.get("name"),
        "full_name": cfg.get("full_name"),
        "family": cfg.get("family"),
        "variant": cfg.get("variant"),
        "manufacturer": (cfg.get("manufacturer") or {}).get("name"),
        "length_m": cfg.get("length"),
        "diameter_m": cfg.get("diameter"),
        "leo_capacity_kg": cfg.get("leo_capacity"),
        "gto_capacity_kg": cfg.get("gto_capacity"),
        "launch_cost_usd": _to_int(cfg.get("launch_cost")),
        "reusable": cfg.get("reusable"),
        "successful_launches": cfg.get("successful_launches"),
        "failed_launches": cfg.get("failed_launches"),
        "total_launch_count": cfg.get("total_launch_count"),
        "maiden_flight": cfg.get("maiden_flight"),
        "image": cfg.get("image_url"),
        "wiki_url": cfg.get("wiki_url"),
        "info_url": cfg.get("info_url"),
        "description": cfg.get("description"),
    }


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# 저장
# ----------------------------------------------------------------------------
def _previous_record_count(filename: str) -> int | None:
    """직전 fetch 산출물의 레코드 수를 반환 (없으면 None).
    파일명 패턴 {TODAY}_xxx → {YYYYMMDD}_xxx 의 마지막 항목을 찾는다."""
    # filename 의 TODAY 접두사 (8자리) 를 와일드카드로
    if len(filename) > 8 and filename[:8].isdigit():
        pattern = "*" + filename[8:]
    else:
        pattern = "*" + filename
    candidates = sorted(p for p in RAW_DIR.glob(pattern) if p.name != filename)
    if not candidates:
        return None
    try:
        with candidates[-1].open(encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("_meta") or {}).get("record_count") or len(data.get("records") or [])
    except Exception:
        return None


def save_with_meta(filename: str, records: list[dict], reliability: str,
                   source: str, source_url: str, scope: str, axis: list[str] | None = None) -> Path:
    # ── 무결성 검증: 새 레코드 0건이면 이전 산출물 보존, 새 파일 쓰지 않음 ──
    if len(records) == 0:
        prev = _previous_record_count(filename)
        msg = f"⚠ 무결성 경고: {filename} 새 레코드 0건"
        if prev:
            msg += f" (직전 {prev}건) — 이전 산출물 보존, 새 파일 미작성"
            print(msg, file=sys.stderr)
            raise RuntimeError(msg)
        else:
            msg += " (직전 산출물도 없음) — 빈 파일 작성"
            print(msg, file=sys.stderr)
    # ── 무결성 검증: 직전 대비 50% 이상 감소 시 경고 (파이프라인은 계속 진행) ──
    prev = _previous_record_count(filename)
    if prev and len(records) < prev * 0.5:
        ratio = len(records) / prev * 100
        print(f"⚠ 무결성 경고: {filename} {len(records)}건 (직전 {prev}건, {ratio:.0f}%) — 데이터 급감 의심",
              file=sys.stderr)

    payload = {
        "_meta": {
            "fetched_at": NOW_ISO,
            "source": source,
            "source_url": source_url,
            "reliability": reliability,
            "scope": scope,
            "axis": axis or ["territory", "comms", "energy_logistics", "defense"],
            "record_count": len(records),
            "schema_version": "1.0",
            "integrity_prev_count": prev,
        },
        "records": records,
    }
    out = RAW_DIR / filename
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    copy_to_sources(out, project="rocket")
    return out


# ----------------------------------------------------------------------------
# sources/ 자동 분류 (CLAUDE.md §8 신뢰도 체계)
# ----------------------------------------------------------------------------
def copy_to_sources(filepath, project: str = "rocket") -> None:
    """
    파일명 패턴으로 신뢰도 등급 판단 후
    data/{project}/sources/{grade}/ 에 복사.
    curated 키워드 포함 파일에 한해 사이드카 .NOTE.txt 도 생성.
    """
    filepath = Path(filepath)
    filename = filepath.name

    if any(k in filename for k in [
        "launchlibrary2", "upcoming", "history",
        "specs_launchlibrary", "analysis_TSR",
        "analysis_rocket_metrics", "curated",
    ]):
        grade = "1.5-authoritative"
    elif any(k in filename for k in ["official", "press", "release"]):
        grade = "1-primary"
    else:
        grade = "1.5-authoritative"  # 불명확 → 1.5차 임시

    dest_dir = ROOT / "data" / "sources" / grade
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(filepath, dest_dir)
    print(f"[sources] {filename} → {grade}/")

    if "curated" in filename:
        note_path = dest_dir / f"{filename}.NOTE.txt"
        if not note_path.exists():
            note_path.write_text("분류 필요: curated 출처 확인\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# 단계별 collector
# ----------------------------------------------------------------------------
def collect_upcoming() -> Path:
    print("  [1/5] upcoming launches (전체)…")
    raw = fetch_paginated("/launch/upcoming/", {
        "limit": PAGE_LIMIT,
        "mode": "detailed",
    }, max_pages=3)  # 향후 ~300건이면 충분
    norm = [normalize_launch(x) for x in raw]
    return save_with_meta(
        f"{TODAY}_upcoming_launchlibrary2_launches.json",
        norm,
        reliability="1.5차",
        source="Launch Library 2 (thespacedevs)",
        source_url=f"{LL2_BASE}/launch/upcoming/",
        scope="upcoming",
    )


def collect_history(slug: str, agency_id: int) -> Path:
    print(f"  [history] {slug} (agency_id={agency_id})…")
    raw = fetch_paginated("/launch/previous/", {
        "limit": PAGE_LIMIT,
        "mode": "detailed",
        "lsp__id": agency_id,
    }, max_pages=MAX_PAGES)
    norm = [normalize_launch(x) for x in raw]
    return save_with_meta(
        f"{TODAY}_history_{slug}_launches.json",
        norm,
        reliability="1.5차",
        source="Launch Library 2 (thespacedevs)",
        source_url=f"{LL2_BASE}/launch/previous/?lsp__id={agency_id}",
        scope=f"history:{slug}",
    )


def collect_specs() -> Path:
    print("  [specs] rocket configurations…")
    # 주요 3사 + 친숙 파밀리 위주로 일단 전체 가져오고 필요시 클라이언트에서 필터
    raw = fetch_paginated("/config/launcher/", {
        "limit": PAGE_LIMIT,
    }, max_pages=3)
    norm = [normalize_rocket_config(x) for x in raw]
    return save_with_meta(
        f"{TODAY}_specs_launchlibrary2_rockets.json",
        norm,
        reliability="1.5차",
        source="Launch Library 2 (thespacedevs)",
        source_url=f"{LL2_BASE}/config/launcher/",
        scope="rocket_specs",
    )


# ----------------------------------------------------------------------------
# 폴백 — 직전 영업일 raw 로 stale 처리
# ----------------------------------------------------------------------------
def find_fallback(filename_today: str) -> Path | None:
    """오늘 파일명 (예: 20260508_history_spacex_launches.json) 의 가장 최근 직전 산출물.
    {YYYYMMDD}_xxx 패턴에서 날짜만 와일드카드로 바꿔 검색.
    """
    if len(filename_today) > 8 and filename_today[:8].isdigit():
        pattern = "*" + filename_today[8:]
    else:
        return None
    candidates = sorted(p for p in RAW_DIR.glob(pattern) if p.name != filename_today)
    return candidates[-1] if candidates else None


# ----------------------------------------------------------------------------
# main — collector 별 try/except. 일부 실패해도 나머지는 진행.
# ----------------------------------------------------------------------------
COLLECTORS = [
    ("upcoming",   "{}_upcoming_launchlibrary2_launches.json",   lambda: collect_upcoming()),
    ("spacex",     "{}_history_spacex_launches.json",            lambda: collect_history("spacex", AGENCY_IDS["spacex"])),
    ("rocketlab",  "{}_history_rocketlab_launches.json",         lambda: collect_history("rocketlab", AGENCY_IDS["rocketlab"])),
    ("blueorigin", "{}_history_blueorigin_launches.json",        lambda: collect_history("blueorigin", AGENCY_IDS["blueorigin"])),
    ("firefly",    "{}_history_firefly_launches.json",           lambda: collect_history("firefly", AGENCY_IDS["firefly"])),
    ("specs",      "{}_specs_launchlibrary2_rockets.json",       lambda: collect_specs()),
]


def main() -> int:
    print(f"📡 fetch_rocket_data.py  ({NOW_ISO})")
    print(f"   → {RAW_DIR}")

    # 같은 날 재실행 / launchd 재시도 시 이미 받은 raw 는 보존하고 throttle 헛발질 하지 않는다.
    # 환경변수 TSR_FORCE_FETCH=1 로 강제 재수집 가능.
    force = os.environ.get("TSR_FORCE_FETCH") == "1"

    status: dict[str, dict] = {}
    written: list[Path] = []
    stale: list[str] = []

    for idx, (name, fname_tpl, fn) in enumerate(COLLECTORS, start=1):
        print(f"\n  [{idx}/{len(COLLECTORS)}] {name}")
        fname = fname_tpl.format(TODAY)
        cached = RAW_DIR / fname
        if cached.exists() and cached.stat().st_size > 0 and not force:
            # 오늘자 raw 가 이미 있음 → 캐시 사용 (success_cached). LL2 호출 안 함.
            print(f"  ↪ 캐시 사용 (이미 오늘자 raw 존재): {cached.name}")
            status[name] = {
                "status": "success_cached",
                "file": str(cached.relative_to(ROOT)),
                "filename": cached.name,
            }
            continue
        try:
            p = fn()
            written.append(p)
            status[name] = {
                "status": "success",
                "file": str(p.relative_to(ROOT)),
                "filename": p.name,
            }
        except Exception as e:
            # collector 한 개 실패 → 직전 영업일 산출물로 폴백 (분석은 stale 표시 + latest_file 폴백 사용)
            err_msg = str(e).split("\n")[0][:200]
            fb = find_fallback(fname)
            if fb:
                stale.append(name)
                status[name] = {
                    "status": "fallback",
                    "fallback_to": str(fb.relative_to(ROOT)),
                    "fallback_filename": fb.name,
                    "fallback_age_days": _age_days(fb),
                    "error": err_msg,
                }
                print(f"  ⚠ {name} 실패 → fallback {fb.name} ({err_msg})", file=sys.stderr)
            else:
                status[name] = {"status": "missing", "error": err_msg}
                print(f"  ❌ {name} 실패 — 직전 산출물도 없음 ({err_msg})", file=sys.stderr)
        # collector 간 rate limit 회피 (재시도로 늘어난 backoff 위에 추가)
        time.sleep(REQUEST_DELAY_SEC)

    # 상태 요약 JSON — run.sh / report.py / publish_notion.py 가 읽음
    # success + success_cached 모두 OK 로 간주 (실제 raw 가 오늘자 라는 사실은 같음)
    ok = sum(1 for s in status.values() if s["status"] in ("success", "success_cached"))
    fb = sum(1 for s in status.values() if s["status"] == "fallback")
    missing = sum(1 for s in status.values() if s["status"] == "missing")

    status_path = RAW_DIR / f"{TODAY}_fetch_status.json"
    with status_path.open("w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "generated_at": NOW_ISO,
                "today": TODAY,
                "schema_version": "1.0",
            },
            "summary": {
                "ok": ok,
                "fallback": fb,
                "missing": missing,
                "total": len(COLLECTORS),
                "stale_collectors": stale,
                "all_failed": ok == 0 and fb == 0,
            },
            "collectors": status,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n📋 fetch_status: ok={ok} fallback={fb} missing={missing} → {status_path.relative_to(ROOT)}")

    if ok == 0 and fb == 0:
        print("\n❌ 모든 collector 실패 + fallback 없음 — 후속 단계 진행 불가", file=sys.stderr)
        return 2  # run.sh 가 abort 판단할 코드

    if missing > 0 or fb > 0:
        print(f"\n⚠ 부분 성공 (missing={missing}, fallback={fb}) — 후속 단계는 stale 표시로 진행")
        # 부분 성공 = exit 0 (run.sh 는 status JSON 으로 판단)

    print("\n✅ 수집 완료:")
    for p in written:
        size_kb = p.stat().st_size / 1024
        print(f"   - {p.relative_to(ROOT)}  ({size_kb:,.1f} KB)")
    return 0


def _age_days(p: Path) -> int | None:
    """파일명 첫 8자가 YYYYMMDD 면 오늘과의 차이 (days)."""
    if len(p.name) >= 8 and p.name[:8].isdigit():
        try:
            d = datetime.strptime(p.name[:8], "%Y%m%d")
            today_dt = datetime.strptime(TODAY, "%Y%m%d")
            return (today_dt - d).days
        except ValueError:
            return None
    return None


if __name__ == "__main__":
    sys.exit(main())

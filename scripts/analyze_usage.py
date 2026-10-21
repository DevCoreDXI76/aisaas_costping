"""
1회성 진단 스크립트 — 최근 30일 API 비용을 키(프로젝트)별·모델별·시간대별로 분해한다.
목적: "구독으로 이전 가능한 API 지출이 얼마인가" 판단 (2026-10-21 고도화 브레인스토밍 C안).

실행(Windows, 리포 루트에서):  python scripts\analyze_usage.py
결과: 콘솔 출력 + _local/usage_analysis.json (gitignore 대상, 커밋하지 말 것)
읽기 전용 — Admin API 조회만 하고 아무것도 변경하지 않는다.
"""
import os, sys, json, collections
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
REPO = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
for line in (REPO/".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k,v=line.split("=",1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(REPO/"src"))
import pricing
prices = pricing.load_prices()
KST = timezone(timedelta(hours=9))
now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = (now - timedelta(days=30)).replace(hour=0)
S, E = start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
out = {"period_utc": [S, E]}

AH = {"anthropic-version": "2023-06-01", "x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"]}
def a_get(path, params):
    data, page = [], None
    while True:
        p = dict(params); 
        if page: p["page"] = page
        r = requests.get("https://api.anthropic.com"+path, headers=AH, params=p, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"{path} {r.status_code} {r.text[:300]}")
        b = r.json(); data += b.get("data", [])
        if b.get("has_more") and b.get("next_page"): page = b["next_page"]
        else: return data

# --- Anthropic keys
try:
    keys = a_get("/v1/organizations/api_keys", {"limit": 100})
    keymap = {k["id"]: {"name": k.get("name"), "status": k.get("status"), "workspace_id": k.get("workspace_id"), "created_at": k.get("created_at")} for k in keys}
    out["anthropic_keys"] = keymap
except Exception as e:
    keymap = {}; out["anthropic_keys_err"] = str(e)

# --- Anthropic cost_report (ground truth) by description
try:
    rows = a_get("/v1/organizations/cost_report", {"starting_at": S[:10]+"T00:00:00Z", "ending_at": E, "bucket_width": "1d", "group_by[]": ["workspace_id", "description"]})
    agg = collections.defaultdict(float); tot = 0
    for b in rows:
        for r in b.get("results", []):
            amt = float(r.get("amount") or 0)/100; tot += amt
            agg[(r.get("workspace_id"), r.get("description"))] += amt
    out["anthropic_cost_total_usd"] = round(tot, 2)
    out["anthropic_cost_by_ws_desc"] = sorted([[w, d, round(v,2)] for (w,d),v in agg.items() if v>=0.01], key=lambda x:-x[2])[:40]
except Exception as e:
    out["anthropic_cost_err"] = str(e)

# --- Anthropic usage by key x model, hourly
try:
    rows = a_get("/v1/organizations/usage_report/messages", {"starting_at": S, "ending_at": E, "bucket_width": "1h", "limit": 168, "group_by[]": ["api_key_id", "model"]})
    bykey = collections.defaultdict(lambda: {"cost":0.0, "models":collections.defaultdict(float), "hour_kst":collections.Counter(), "weekend":0.0, "days":set(), "uncached_in":0, "cache_read":0, "cache_write":0, "out":0})
    for b in rows:
        hs = datetime.fromisoformat(b["starting_at"].replace("Z","+00:00")).astimezone(KST)
        for r in b.get("results", []):
            k = r.get("api_key_id") or "(no key: console/workbench 등)"
            m = r.get("model") or "?"
            ui = r.get("uncached_input_tokens") or 0; o = r.get("output_tokens") or 0
            cr = r.get("cache_read_input_tokens") or 0
            cc = r.get("cache_creation") or {}; cw = (cc.get("ephemeral_5m_input_tokens") or 0)+(cc.get("ephemeral_1h_input_tokens") or 0)
            c,_ = pricing.calc_anthropic_cost_usd(m, ui, o, cw, cr, prices)
            c = c or 0
            d = bykey[k]; d["cost"]+=c; d["models"][m]+=c; d["hour_kst"][hs.hour]+=c
            if hs.weekday()>=5: d["weekend"]+=c
            if c>0: d["days"].add(hs.date().isoformat())
            d["uncached_in"]+=ui; d["cache_read"]+=cr; d["cache_write"]+=cw; d["out"]+=o
    res = []
    for k,d in bykey.items():
        if d["cost"] < 0.01: continue
        night = sum(v for h,v in d["hour_kst"].items() if 1<=h<=6)
        work = sum(v for h,v in d["hour_kst"].items() if 9<=h<=17)
        tin = d["uncached_in"]+d["cache_read"]+d["cache_write"]
        res.append({"key": k, "name": (keymap.get(k) or {}).get("name"), "status": (keymap.get(k) or {}).get("status"),
            "est_cost_usd": round(d["cost"],2), "active_days": len(d["days"]),
            "models": {m: round(v,2) for m,v in sorted(d["models"].items(), key=lambda x:-x[1]) if v>=0.01},
            "weekend_pct": round(100*d["weekend"]/d["cost"]), "night_1to6_pct": round(100*night/d["cost"]), "weekday_9to17_pct": round(100*work/d["cost"]),
            "top_hours_kst": [h for h,_ in d["hour_kst"].most_common(4)],
            "cache_read_pct_of_input": round(100*d["cache_read"]/tin) if tin else None,
            "out_tokens": d["out"], "in_tokens_total": tin})
    out["anthropic_by_key"] = sorted(res, key=lambda x:-x["est_cost_usd"])
except Exception as e:
    out["anthropic_usage_err"] = str(e)

# --- OpenAI
OH = {"Authorization": "Bearer "+os.environ.get("OPENAI_ADMIN_KEY","")}
def o_get(path, params):
    data, page = [], None
    while True:
        p = dict(params)
        if page: p["page"] = page
        r = requests.get("https://api.openai.com"+path, headers=OH, params=p, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"{path} {r.status_code} {r.text[:300]}")
        b = r.json(); data += b.get("data", [])
        if b.get("has_more") and b.get("next_page"): page = b["next_page"]
        else: return data
try:
    projs = {p["id"]: p.get("name") for p in o_get("/v1/organization/projects", {"limit": 100})}
except Exception as e:
    projs = {}; out["openai_projects_err"] = str(e)
try:
    st = int(start.timestamp())
    rows = o_get("/v1/organization/costs", {"start_time": st, "bucket_width": "1d", "limit": 31, "group_by": ["project_id", "line_item"]})
    agg = collections.defaultdict(float); tot=0
    for b in rows:
        for r in b.get("results", []):
            v = float((r.get("amount") or {}).get("value") or 0); tot+=v
            agg[(projs.get(r.get("project_id"), r.get("project_id")), r.get("line_item"))]+=v
    out["openai_cost_total_usd"] = round(tot,2)
    out["openai_by_project_item"] = sorted([[p,i,round(v,2)] for (p,i),v in agg.items() if v>=0.01], key=lambda x:-x[2])
except Exception as e:
    out["openai_cost_err"] = str(e)

(lambda p: (p.parent.mkdir(exist_ok=True), p)[1])(REPO/"_local"/"usage_analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))

#!/usr/bin/env python3
"""MCP server over the job-search Supabase tracker.

A pure-stdlib JSON-RPC 2.0 server on stdio (the MCP stdio transport:
newline-delimited JSON, one message per line). It exposes the tracker as
typed tools so a Claude session — the scheduled hourly routine or an
interactive one — reads and writes the DB through tools instead of ad-hoc
scripts.

Run: python backend/mcp_server/tracker_mcp.py
Env: the same SUPABASE_URL / SUPABASE_KEY the backend uses.

Only additive/safe operations are exposed; there is no destructive
"delete application" tool by design.
"""

import json
import os
import sys
import traceback

# Make backend/modules importable no matter the launch cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULES = os.path.join(os.path.dirname(_HERE), "modules")
if _MODULES not in sys.path:
    sys.path.insert(0, _MODULES)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "jobsearch-tracker", "version": "1.0.0"}


def _log(*a):
    print("[tracker-mcp]", *a, file=sys.stderr, flush=True)


def _df_records(df):
    """DataFrame (or already-list) -> list of plain dicts."""
    if df is None:
        return []
    if isinstance(df, list):
        return df
    try:
        if hasattr(df, "empty"):
            if df.empty:
                return []
            return json.loads(df.to_json(orient="records"))
    except Exception:
        pass
    return df


# ---------------------------------------------------------------- tool impls
def _t_list_applications(args):
    import tracker
    rows = _df_records(tracker.get_all_applications())
    status = (args.get("status") or "").strip()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def _t_add_application(args):
    import tracker
    if not args.get("company") or not args.get("role"):
        raise ValueError("company and role are required")
    tracker.add_application(
        company=args["company"],
        role=args["role"],
        job_type=args.get("job_type", "Full-time"),
        platform=args.get("platform", ""),
        url=args.get("url", ""),
        salary=args.get("salary", ""),
        notes=args.get("notes", ""),
    )
    return {"ok": True, "company": args["company"], "role": args["role"]}


def _t_update_application_status(args):
    import tracker
    app_id = args.get("app_id")
    status = args.get("status")
    if app_id is None or not status:
        raise ValueError("app_id and status are required")
    tracker.update_status(int(app_id), status)
    return {"ok": True, "app_id": int(app_id), "status": status}


def _t_snooze_follow_up(args):
    import tracker
    tracker.snooze_follow_up(int(args["app_id"]), args["new_date"])
    return {"ok": True, "app_id": int(args["app_id"]), "follow_up_date": args["new_date"]}


def _t_get_follow_ups_due(args):
    import tracker
    return _df_records(tracker.get_post_connection_follow_ups_due())


def _t_get_stats(args):
    import tracker
    return tracker.get_stats()


def _t_get_platform_effectiveness(args):
    import tracker
    return _df_records(tracker.get_platform_effectiveness())


def _t_list_scraped_jobs(args):
    import tracker
    rows = tracker.get_scraped_jobs(source=args.get("source"))
    rows = _df_records(rows)
    limit = int(args.get("limit", 50))
    return rows[:limit]


def _t_get_job_message(args):
    import tracker
    row = tracker.get_job_message(
        int(args["scraped_job_id"]),
        message_type=args.get("message_type", "cold_dm"),
    )
    return row or {"found": False}


def _t_save_job_message(args):
    import tracker
    content = (args.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    ok = tracker.save_job_message(
        int(args["scraped_job_id"]),
        content,
        message_type=args.get("message_type", "cold_dm"),
    )
    return {"ok": bool(ok)}


def _t_list_message_requests(args):
    import tracker
    return _df_records(tracker.get_message_requests(status=args.get("status")))


def _t_get_company_intel(args):
    import tracker
    row = tracker.get_cached_research(args["company"])
    return row or {"found": False}


def _t_save_company_intel(args):
    import tracker
    payload = {
        "product_url": args.get("product_url", ""),
        "hiring_contact": {
            "name": args.get("hiring_contact_name", ""),
            "title": args.get("hiring_contact_title", ""),
            "linkedin_url": args.get("hiring_contact_linkedin", ""),
        },
    }
    tracker.save_research_cache(args["company"], payload)
    return {"ok": True, "company": args["company"]}


# ------------------------------------------------------------------- schemas
TOOLS = [
    {
        "name": "list_applications",
        "description": "List tracked job applications, newest first. Optional status filter.",
        "handler": _t_list_applications,
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string", "description": "e.g. Applied, Interviewing, Offer, Rejected"}},
        },
    },
    {
        "name": "add_application",
        "description": "Add a job to the tracker. follow_up_date is auto-set to +7 days.",
        "handler": _t_add_application,
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "role": {"type": "string"},
                "job_type": {"type": "string"},
                "platform": {"type": "string"},
                "url": {"type": "string"},
                "salary": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["company", "role"],
        },
    },
    {
        "name": "update_application_status",
        "description": "Change one application's status.",
        "handler": _t_update_application_status,
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {"type": "integer"},
                "status": {"type": "string"},
            },
            "required": ["app_id", "status"],
        },
    },
    {
        "name": "snooze_follow_up",
        "description": "Push an application's follow-up date to a new YYYY-MM-DD.",
        "handler": _t_snooze_follow_up,
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {"type": "integer"},
                "new_date": {"type": "string"},
            },
            "required": ["app_id", "new_date"],
        },
    },
    {
        "name": "get_follow_ups_due",
        "description": "Applications whose follow-up date has arrived at least seven Asia/Kolkata calendar days after a recorded LinkedIn connection invitation (non-terminal), most overdue first.",
        "handler": _t_get_follow_ups_due,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_stats",
        "description": "Aggregate application stats (totals, by status/type/platform).",
        "handler": _t_get_stats,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_platform_effectiveness",
        "description": "Response/interview rates per source platform.",
        "handler": _t_get_platform_effectiveness,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_scraped_jobs",
        "description": "Recently scraped jobs. Optional source filter and limit.",
        "handler": _t_list_scraped_jobs,
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_job_message",
        "description": "Get a stored generated message for a scraped job (cold_dm, resume_points, demo_html).",
        "handler": _t_get_job_message,
        "inputSchema": {
            "type": "object",
            "properties": {
                "scraped_job_id": {"type": "integer"},
                "message_type": {"type": "string"},
            },
            "required": ["scraped_job_id"],
        },
    },
    {
        "name": "save_job_message",
        "description": "Store/replace a generated message for a scraped job.",
        "handler": _t_save_job_message,
        "inputSchema": {
            "type": "object",
            "properties": {
                "scraped_job_id": {"type": "integer"},
                "content": {"type": "string"},
                "message_type": {"type": "string"},
            },
            "required": ["scraped_job_id", "content"],
        },
    },
    {
        "name": "list_message_requests",
        "description": "Content-generation queue entries. Optional status filter (pending/ready/failed).",
        "handler": _t_list_message_requests,
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    },
    {
        "name": "get_company_intel",
        "description": "Cached company research for a company name, or {found:false}.",
        "handler": _t_get_company_intel,
        "inputSchema": {
            "type": "object",
            "properties": {"company": {"type": "string"}},
            "required": ["company"],
        },
    },
    {
        "name": "save_company_intel",
        "description": "Upsert a verified company website and hiring contact.",
        "handler": _t_save_company_intel,
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "product_url": {"type": "string"},
                "hiring_contact_name": {"type": "string"},
                "hiring_contact_title": {"type": "string"},
                "hiring_contact_linkedin": {"type": "string"},
            },
            "required": ["company"],
        },
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def _public_tools():
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


# --------------------------------------------------------------- rpc plumbing
def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg):
    """Return a response dict, or None for notifications."""
    method = msg.get("method")
    id_ = msg.get("id")

    if method == "initialize":
        return _result(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": _public_tools()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _BY_NAME.get(name)
        if not tool:
            return _error(id_, -32602, f"unknown tool: {name}")
        try:
            data = tool["handler"](args)
            text = json.dumps(data, default=str, ensure_ascii=False)
            return _result(id_, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            _log("tool error", name, repr(e))
            _log(traceback.format_exc())
            return _result(id_, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    if id_ is not None:
        return _error(id_, -32601, f"method not found: {method}")
    return None


def main():
    _log("started; waiting on stdio")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _log("bad json line, skipping")
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            resp = _error(msg.get("id"), -32603, f"internal error: {e}")
            _log("handler crash", repr(e))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

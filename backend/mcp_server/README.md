# Tracker MCP server

A pure-stdlib [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes the job-search Supabase tracker as typed tools, so a
Claude session (the hourly routine or an interactive one) can read and
write the tracker through tools instead of ad-hoc scripts.

## Run

```bash
SUPABASE_URL=... SUPABASE_KEY=... python3 backend/mcp_server/tracker_mcp.py
```

It speaks JSON-RPC 2.0 over stdio (the MCP stdio transport: one JSON
message per line). It needs the backend's Python deps installed
(`backend/requirements.txt`) and the same `SUPABASE_URL` / `SUPABASE_KEY`
the backend uses.

## Wiring

`.mcp.json` at the repo root registers this server for Claude Code, and
`.claude/settings.json` sets `enableAllProjectMcpServers` so it loads
without a per-session trust prompt. The `SUPABASE_URL` / `SUPABASE_KEY`
values are read from the environment (`${VAR}` expansion) — no secrets are
committed.

## Tools

Read: `list_applications`, `get_follow_ups_due`, `get_stats`,
`get_platform_effectiveness`, `list_scraped_jobs`, `get_job_message`,
`list_message_requests`, `get_company_intel`.

Write (additive only — there is deliberately no delete-application tool):
`add_application`, `update_application_status`, `snooze_follow_up`,
`save_job_message`, `save_company_intel` (website and hiring contact only).
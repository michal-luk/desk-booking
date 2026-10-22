# MCP server for AI assistants

The app has a built-in [Model Context Protocol](https://modelcontextprotocol.io) server. Connect an AI assistant
and book in plain language:

> *Book me a desk next Tuesday near the Sales team.*
> *Is Room Ocean free tomorrow 10–11? If so, book it for “Sprint planning”.*
> *Who from my team is in the office this week?*
> *Cancel all my bookings for next week.*

- Address: `{APP_URL}/mcp` — transport **Streamable HTTP** (JSON responses), protocol version `2025-06-18`
  (older `2025-03-26` and `2024-11-05` accepted).
- Authentication: `Authorization: Bearer <personal API key>` from **My account**.
- The assistant acts **with the permissions of the key owner** — exactly like the user in the browser — and gets
  messages in the owner's language.
- The administrator can switch the MCP server off in *Settings → API and MCP*. Requests from a foreign browser
  `Origin` are rejected (DNS-rebinding protection).

## Connecting

**Claude Code**

```bash
claude mcp add --transport http desk-booking https://desks.example.com/desks/mcp \
  --header "Authorization: Bearer rzb_..."
```

**Claude Desktop, VS Code (Copilot), Cursor and other clients with a JSON config**

```json
{
  "mcpServers": {
    "desk-booking": {
      "type": "http",
      "url": "https://desks.example.com/desks/mcp",
      "headers": { "Authorization": "Bearer rzb_..." }
    }
  }
}
```

**Other platforms** (ChatGPT connectors, n8n, NeoDesk AI Studio, own agents) — add an MCP server with the URL above
and a bearer token. The **My account** page shows ready-to-copy snippets with your own address and key.

## Tools

| Tool | Effect | Description |
|---|---|---|
| `whoami` | read | Who I am: role, teams, people I can book for |
| `list_floors` | read | Floors / locations with ids |
| `find_free_places` | read | Desks, rooms and booths I can book on a day (optionally hours, type, floor, minimum capacity) |
| `floor_plan` | read | Status of every place on a floor on a day: free, taken (by whom), mine |
| `place_availability` | read | Is a given place free, day by day in a date range |
| `place_schedule` | read | Bookings of one place for the next days |
| `find_person` | read | Where a person sits on a given day |
| `team_week` | read | My team's office week |
| `my_bookings` | read | My upcoming and recent bookings with ids |
| `book` | write | Book a place for a day or a date range, optionally for hours, with a subject, for a team member |
| `cancel_bookings` | write, destructive | Cancel bookings by id |
| `all_bookings` | read | Administrators only: all bookings with filters |

Places can be referred to by id or by code / name (`"A-12"`, `"Room Ocean"`). Tools carry MCP annotations
(`readOnlyHint`, `destructiveHint`), so clients can ask for confirmation before writing. Failed bookings are
returned as tool errors (`isError: true`) with the reason, e.g. *the place is taken* or *you already have a desk that day*.

The server's `instructions` tell the assistant today's date, the logged-in user and their language, so relative
dates (“tomorrow”, “next Tuesday”) work out of the box.

"""Zero-dependency local web server for the draft board."""

from __future__ import annotations

import json
import logging
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import SleeperClient, SleeperError
from .session import Session

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class Handler(BaseHTTPRequestHandler):
    session: Session
    client: SleeperClient

    server_version = "draftkit"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- plumbing -------------------------------------------------------

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404, "Not found")
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- routes ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if route == "/":
                self._send_file(WEB_ROOT / "index.html")
            elif route.startswith("/static/"):
                # Resolve inside WEB_ROOT only; reject traversal attempts.
                target = (WEB_ROOT / route[len("/static/"):]).resolve()
                if WEB_ROOT.resolve() in target.parents:
                    self._send_file(target)
                else:
                    self.send_error(403, "Forbidden")
            elif route == "/api/leagues":
                self._send_json(self._leagues(query))
            elif route == "/api/status":
                self._send_json(self.session.status())
            elif route == "/api/live":
                self._send_json(self.session.live())
            elif route == "/api/board":
                self._send_json(self.session.board_payload())
            else:
                self.send_error(404, "Not found")
        except SleeperError as exc:
            self._send_json({"error": str(exc)}, status=502)
        except Exception as exc:  # never let one bad request kill the board
            log.exception("GET %s failed", route)
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        body = self._body()
        try:
            if route == "/api/connect":
                league_id = str(body.get("league_id") or "").strip()
                draft_id = str(body.get("draft_id") or "").strip() or None
                username = str(body.get("username") or "").strip() or None
                if not league_id and draft_id:
                    raw = self.client.draft(draft_id)
                    league_id = str((raw or {}).get("league_id") or "")
                if not league_id:
                    raise SleeperError("Provide a league or a draft to connect to.")
                self._send_json(self.session.connect(league_id, draft_id, username))
            elif route == "/api/slot":
                self.session.set_slot(int(body.get("slot") or 0))
                self._send_json(self.session.status())
            elif route == "/api/mark":
                self.session.mark_drafted(
                    str(body.get("player_id") or ""), bool(body.get("drafted"))
                )
                self._send_json({"ok": True})
            elif route == "/api/sync":
                self.session.sync(force=True)
                self._send_json(self.session.live())
            else:
                self.send_error(404, "Not found")
        except SleeperError as exc:
            self._send_json({"error": str(exc)}, status=502)
        except Exception as exc:
            log.exception("POST %s failed", route)
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    # ---- helpers --------------------------------------------------------

    def _leagues(self, query: dict) -> dict:
        username = (query.get("username") or [""])[0].strip()
        if not username:
            return {"error": "username required"}
        season = (query.get("season") or [""])[0].strip()
        if not season:
            season = str(self.client.state().get("season") or "")

        user = self.client.user(username)
        if not user:
            return {"error": f"No Sleeper user named '{username}'"}
        user_id = str(user.get("user_id"))
        leagues = self.client.user_leagues(user_id, season)

        rows = []
        for league in leagues:
            league_id = str(league.get("league_id"))
            drafts = self.client.league_drafts(league_id)
            drafts.sort(key=lambda d: int(d.get("start_time") or 0), reverse=True)
            draft = drafts[0] if drafts else {}
            rows.append(
                {
                    "league_id": league_id,
                    "name": league.get("name"),
                    "teams": league.get("total_rosters"),
                    "status": league.get("status"),
                    "draft_id": str(draft.get("draft_id") or ""),
                    "draft_status": draft.get("status"),
                    "draft_type": draft.get("type"),
                }
            )
        return {"user_id": user_id, "season": season, "username": username, "leagues": rows}


def serve(session: Session, client: SleeperClient, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"session": session, "client": client})
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd

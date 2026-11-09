"""Reports of comments, pages and AI replies (gamma/reports.py,
docs/dev/api.md "Reports"): anyone who can read what they report files
one; this server's admins list, resolve and delete them.

Sync handlers: a report is a users.db write, and its read of a comment or
page opens the workspace's pages.db.
"""

from fastapi import APIRouter, Request

from .. import reports
from ..auth import require_admin
from ..ops import StorableBody

router = APIRouter(prefix="/api", tags=["reports"])


class ReportBody(StorableBody):
    kind: str
    reason: str
    details: str = ""
    page_id: str = ""     # a page report's page
    comment_id: str = ""  # a comment report's comment
    content: str = ""     # an AI reply's text
    model: str = ""       # and the model that wrote it
    link: str = ""        # a published page's address, as the browser is on it


class ReportState(StorableBody):
    state: str


@router.post("/reports", status_code=201)
def file_report(payload: ReportBody, request: Request):
    """Report a comment, a page or an AI reply → ``{id, forwarded}``:
    ``forwarded`` whether it goes on to Gamma Cloud too. A share link's
    visitor reports through its ``?share=``, as they read."""
    row = reports.file(request, **payload.model_dump())
    return {"id": row["id"], "forwarded": reports.forwards()}


@router.get("/admin/reports")
def list_reports(request: Request, state: str = "all"):
    """Admins: ``{reports, open, forwards}``, newest first."""
    require_admin(request)
    return reports.listing(state)


@router.patch("/admin/reports/{report_id}")
def patch_report(report_id: str, payload: ReportState, request: Request):
    """Admins: resolve a report (``{state: "resolved"}``) or open it again
    → the report."""
    require_admin(request)
    return {"report": reports.set_state(report_id, payload.state, request.state.user_id)}


@router.delete("/admin/reports/{report_id}")
def delete_report(report_id: str, request: Request):
    require_admin(request)
    reports.delete(report_id)
    return {"ok": True}

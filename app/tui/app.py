"""The Textual TUI application."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Input, Label, ListItem, ListView, Static

from ..db import SessionLocal, init_db
from ..db_models import Vendor
from ..extraction import loader, template
from ..extraction.fields import group_lines
from ..models import MappingIn, TemplateIn
from .. import repo


def reconstruct_page(page: "loader.PageContent", cols: int = 110) -> str:
    """Lay the page's words onto a character grid scaled from their positions.

    Not pixel-perfect, but it preserves left-to-right order and rough columns so a
    human can see where labels and values sit — enough to map fields by keyboard.
    """
    width = page.width or 595.0
    out: List[str] = []
    for line in group_lines(page.words):
        row = [" "] * cols
        for w in line.words:
            col = max(0, min(cols - 1, int(w.x0 / width * cols)))
            for i, ch in enumerate(w.text):
                if col + i < cols:
                    row[col + i] = ch
        out.append("".join(row).rstrip())
    return "\n".join(out) or "(no extractable text — likely a scan)"


class LesarinTui(App):
    """Teach a vendor template from the keyboard."""

    CSS = """
    #left { width: 26; border-right: solid $panel-darken-1; }
    #center { width: 2fr; }
    #right { width: 64; border-left: solid $panel-darken-1; }
    #page { padding: 0 1; }
    DataTable { height: 1fr; }
    Input { margin: 0 0 1 0; }
    """

    BINDINGS = [
        ("r", "read", "Read"),
        ("e", "edit_label", "Edit label"),
        ("s", "save", "Save"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, pdf_path: Optional[str] = None, pdf_bytes: Optional[bytes] = None) -> None:
        super().__init__()
        if pdf_bytes is None and pdf_path:
            pdf_bytes = Path(pdf_path).read_bytes()
        self._pdf_bytes = pdf_bytes or b""
        self.document: Optional[loader.Document] = None
        self.mappings: List[dict] = []
        self.results: Dict[str, Tuple[Optional[str], float, str]] = {}
        self.current_vendor_id: Optional[int] = None

    # ---- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="left"):
                yield Label("Vendors")
                yield ListView(id="vendors")
            with VerticalScroll(id="center"):
                yield Static("", id="page", expand=True)
            with Vertical(id="right"):
                yield Input(placeholder="Vendor name (e.g. Effo)", id="vname")
                yield Input(placeholder="V-tal / ID (e.g. 314188)", id="vtal")
                yield DataTable(id="maps", cursor_type="row")
                yield Input(placeholder="source label for selected row — Enter applies", id="label")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Lesarin — vendor template editor (TUI)"
        init_db()
        self.session = SessionLocal()

        table = self.query_one("#maps", DataTable)
        table.add_columns("Field", "How", "Label", "Value", "Conf")

        self.document = loader.load(self._pdf_bytes) if self._pdf_bytes else None
        self._render_page()
        self._reload_vendors()

        vendor = None
        if self.document:
            vendor = repo.detect_vendor(self.session, template.document_text(self.document))
        if vendor is not None:
            self._load_vendor(vendor)
        else:
            self._blank_template()
        self.do_read()

    # ---- helpers ----------------------------------------------------------

    def _render_page(self) -> None:
        page_widget = self.query_one("#page", Static)
        if self.document and self.document.pages:
            page_widget.update(reconstruct_page(self.document.pages[0]))
        else:
            page_widget.update("Pass a PDF path: python -m app.tui invoice.pdf")

    def _reload_vendors(self) -> None:
        lv = self.query_one("#vendors", ListView)
        lv.clear()
        self._vendor_ids: List[int] = []
        for v in repo.list_vendors(self.session):
            self._vendor_ids.append(v.id)
            lv.append(ListItem(Label(f"{v.name}  ·  {v.identifier}")))

    def _blank_template(self) -> None:
        self.current_vendor_id = None
        self.query_one("#vname", Input).value = ""
        self.query_one("#vtal", Input).value = ""
        self.mappings = [
            {
                "output": f.key,
                "strategy": "label",
                "label": "",
                "relation": "right",
                "value_type": f.value_type,
                "page": None,
                "bbox": None,
            }
            for f in repo.list_output_fields(self.session)
        ]

    def _load_vendor(self, vendor: Vendor) -> None:
        self.current_vendor_id = vendor.id
        self.query_one("#vname", Input).value = vendor.name
        self.query_one("#vtal", Input).value = vendor.identifier
        self.mappings = [
            {
                "output": m.output_key,
                "strategy": m.strategy,
                "label": m.source_label or "",
                "relation": m.relation,
                "value_type": m.value_type,
                "page": m.page,
                "bbox": m.bbox,
            }
            for m in vendor.mappings
        ]

    def _refresh_table(self) -> None:
        table = self.query_one("#maps", DataTable)
        cursor = table.cursor_row
        table.clear()
        for m in self.mappings:
            res = self.results.get(m["output"])
            value = res[0] if res else None
            conf = f"{int(res[1] * 100)}%" if res and res[0] else ""
            table.add_row(m["output"], m["strategy"], m.get("label") or "—", value or "—", conf)
        if self.mappings:
            table.move_cursor(row=max(0, min(cursor, len(self.mappings) - 1)))

    def do_read(self) -> None:
        if not self.document:
            return
        tmpl = TemplateIn(fields=[MappingIn(**m) for m in self.mappings])
        located = template.apply_template(self.document, tmpl)
        self.results = {f.output: (f.value, f.confidence, f.source) for f in located}
        found = sum(1 for f in located if f.found)
        self.sub_title = f"{found}/{len(located)} fields located"
        self._refresh_table()

    def set_label_for(self, output: str, label: str) -> None:
        """Programmatic mapping edit (used by tests and the label input)."""
        for m in self.mappings:
            if m["output"] == output:
                m["label"] = label
                m["strategy"] = "label"
                break
        self.do_read()

    # ---- actions ----------------------------------------------------------

    def action_read(self) -> None:
        self.do_read()

    def action_edit_label(self) -> None:
        table = self.query_one("#maps", DataTable)
        i = table.cursor_row
        if 0 <= i < len(self.mappings):
            inp = self.query_one("#label", Input)
            inp.value = self.mappings[i].get("label") or ""
            inp.focus()

    def action_save(self) -> None:
        name = self.query_one("#vname", Input).value.strip()
        vtal = self.query_one("#vtal", Input).value.strip()
        if not name or not vtal:
            self.notify("Vendor needs a name and a V-tal.", severity="warning")
            return
        if self.current_vendor_id:
            repo.update_vendor(self.session, self.current_vendor_id, name=name, identifier=vtal, mappings=self.mappings)
        else:
            saved = repo.create_vendor(self.session, identifier=vtal, name=name, mappings=self.mappings)
            self.current_vendor_id = saved.id
        self._reload_vendors()
        self.notify(f"Saved template for {name}.")

    # ---- events -----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "label":
            return
        table = self.query_one("#maps", DataTable)
        i = table.cursor_row
        if 0 <= i < len(self.mappings):
            self.mappings[i]["label"] = event.value.strip()
            self.mappings[i]["strategy"] = "label"
            self.do_read()
        event.input.value = ""
        table.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self._vendor_ids):
            return
        vendor = repo.get_vendor(self.session, self._vendor_ids[idx])
        if vendor is not None:
            self._load_vendor(vendor)
            self.do_read()


def main() -> None:
    pdf = sys.argv[1] if len(sys.argv) > 1 else None
    LesarinTui(pdf_path=pdf).run()

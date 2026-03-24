"""Textual TUI for the Ishmael orchestrator."""

from __future__ import annotations

import logging
import threading
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from .config import Config
from .orchestrator import Orchestrator


class RichLogHandler(logging.Handler):
    """Routes log records into a Textual RichLog widget."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_widget = self._app.query_one("#log", RichLog)
        except Exception:
            return
        msg = self.format(record)
        level = record.levelno
        if level >= logging.ERROR:
            markup = f"[red]{msg}[/red]"
        elif level >= logging.WARNING:
            markup = f"[yellow]{msg}[/yellow]"
        else:
            markup = msg

        # call_from_thread is only safe from non-main threads
        if threading.get_ident() != self._app._thread_id:
            self._app.call_from_thread(log_widget.write, markup)
        else:
            log_widget.write(markup)


class StatsBar(Static):
    """Displays orchestrator stats in a single line."""

    def update_stats(self, state: dict) -> None:
        active = state.get("active", 0)
        max_ag = state.get("max", 0)
        ready = state.get("ready", "?")
        completed = state.get("completed", 0)
        failed = state.get("failed", 0)
        self.update(
            f"Active: {active} (max {max_ag})  |  Queued: {ready}  |  "
            f"Done: {completed}  |  Failed: {failed}"
        )


class IshmaelApp(App):
    """Ishmael orchestrator TUI."""

    TITLE = "Ishmael"
    CSS = """
    StatsBar {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #beads {
        width: 30%;
        min-width: 25;
    }
    #detail {
        width: 70%;
        border-left: solid $accent;
    }
    #log {
        height: 5;
        border-top: solid $accent;
    }
    """
    BINDINGS = [("q", "quit", "Quit"), ("c", "copy_detail", "Copy")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.orch = Orchestrator(config)
        self._agent_start_times: dict[str, float] = {}
        self._selected_bead_id: str | None = None
        self._last_output_index: int = 0
        self._bead_data: dict[str, dict] = {}
        self._running_bead_ids: set[str] = set()
        self._rebuilding_table: bool = False
        self._detail_lines: list[str] = []

        # Wire up orchestrator callbacks
        self.orch.on_agent_started = self._on_agent_started
        self.orch.on_agent_completed = self._on_agent_completed
        self.orch.on_agent_failed = self._on_agent_failed

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatsBar(id="stats")
        with Horizontal(id="main"):
            yield DataTable(id="beads", cursor_type="row")
            yield RichLog(id="detail", highlight=True, markup=True, wrap=True)
        yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        # Replace only stream handlers, keep file handlers etc.
        root = logging.getLogger()
        root.handlers = [
            h for h in root.handlers
            if not isinstance(h, logging.StreamHandler)
        ]
        handler = RichLogHandler(self)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(handler)

        table = self.query_one("#beads", DataTable)
        table.add_columns("ID", "Title", "Status")
        self.query_one("#log", RichLog).write("[bold]Ishmael started[/bold]")
        self._poll()
        self.set_interval(self.config.poll_interval, self._poll)
        self.set_interval(1, self._stream_output)

    def _poll(self) -> None:
        """Run one orchestrator poll cycle in a worker thread."""
        self.run_worker(self._poll_worker, exclusive=True, thread=True)

    def _poll_worker(self) -> None:
        state = self.orch.poll_once()
        self.call_from_thread(self._update_ui, state)

    def _update_ui(self, state: dict) -> None:
        # Update stats bar
        self.query_one("#stats", StatsBar).update_stats(state)

        # Track running bead IDs
        self._running_bead_ids = {
            ag_info["bead_id"] for ag_info in state.get("agents", [])
        }

        # Rebuild beads table — suppress spurious RowHighlighted events
        self._rebuilding_table = True
        try:
            table = self.query_one("#beads", DataTable)
            prev_selected = self._selected_bead_id
            table.clear()
            self._bead_data.clear()

            now = time.time()
            row_index_to_select = None
            row_count = 0

            # Running agents first
            for ag_info in state.get("agents", []):
                bid = ag_info["bead_id"]
                start = self._agent_start_times.get(bid, now)
                elapsed = int(now - start)
                m, s = divmod(elapsed, 60)
                status_str = f"running ({m}m {s:02d}s)"
                table.add_row(bid, "", status_str, key=bid)
                self._bead_data[bid] = {"id": bid, "title": "", "status": "running"}
                if bid == prev_selected:
                    row_index_to_select = row_count
                row_count += 1

            # All beads from the database
            for bead in state.get("beads", []):
                bid = bead.get("id", "")
                if bid in self._running_bead_ids:
                    continue
                title = bead.get("title", "")
                status = bead.get("status", "")
                table.add_row(bid, title, status, key=bid)
                self._bead_data[bid] = bead
                if bid == prev_selected:
                    row_index_to_select = row_count
                row_count += 1

            # Restore selection
            if row_index_to_select is not None:
                table.move_cursor(row=row_index_to_select)
        finally:
            self._rebuilding_table = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row selection in the beads table."""
        if self._rebuilding_table:
            return
        new_id = str(event.row_key.value) if event.row_key else None
        if new_id == self._selected_bead_id:
            return
        self._selected_bead_id = new_id
        self._last_output_index = 0
        self._clear_detail()

        if new_id and new_id in self._running_bead_ids:
            self._write_detail(f"[bold]Streaming output for {new_id}...[/bold]\n")
        elif new_id:
            self._show_bead_details(new_id)

    def _write_detail(self, text: str) -> None:
        """Write to the detail panel and track content for copying."""
        self._detail_lines.append(text)
        self.query_one("#detail", RichLog).write(text)

    def _clear_detail(self) -> None:
        """Clear the detail panel and its content buffer."""
        self._detail_lines.clear()
        self.query_one("#detail", RichLog).clear()

    def action_copy_detail(self) -> None:
        """Copy the detail panel content to the system clipboard."""
        if not self._detail_lines:
            self.notify("Nothing to copy", severity="warning")
            return
        self.copy_to_clipboard("\n".join(self._detail_lines))
        self.notify("Copied to clipboard")

    def _show_bead_details(self, bead_id: str) -> None:
        """Display bead description, notes, and metadata in the detail panel."""
        bead = self._bead_data.get(bead_id, {})

        self._write_detail(f"[bold]Bead: {bead_id}[/bold]")
        if title := bead.get("title"):
            self._write_detail(f"[bold]Title:[/bold] {title}")
        if status := bead.get("status"):
            self._write_detail(f"[bold]Status:[/bold] {status}")
        if priority := bead.get("priority"):
            self._write_detail(f"[bold]Priority:[/bold] {priority}")
        if desc := bead.get("description"):
            self._write_detail(f"\n[bold]Description:[/bold]\n{desc}")
        if notes := bead.get("notes"):
            self._write_detail(f"\n[bold]Notes:[/bold]\n{notes}")
        meta = bead.get("metadata")
        if meta:
            self._write_detail(f"\n[bold]Metadata:[/bold]\n{meta}")

    def _stream_output(self) -> None:
        """Timer callback: append new agent output lines to detail panel."""
        bead_id = self._selected_bead_id
        if not bead_id or bead_id not in self._running_bead_ids:
            return

        agent = self.orch.get_agent(bead_id)
        if agent is None:
            return

        with agent._output_lock:
            new_lines = agent.output_lines[self._last_output_index:]
        if not new_lines:
            return

        for line in new_lines:
            self._write_detail(line)
        self._last_output_index += len(new_lines)

    def _on_agent_started(self, bead_id: str) -> None:
        start_time = time.time()

        def _update() -> None:
            self._agent_start_times[bead_id] = start_time
            self.query_one("#log", RichLog).write(
                f"[green]Started[/green] agent for {bead_id}"
            )

        self.call_from_thread(_update)

    def _on_agent_completed(self, bead_id: str, output: str) -> None:
        def _update() -> None:
            self._agent_start_times.pop(bead_id, None)
            self.query_one("#log", RichLog).write(
                f"[blue]Completed[/blue] {bead_id}: {output[:200]}"
            )

        self.call_from_thread(_update)

    def _on_agent_failed(self, bead_id: str, error: str) -> None:
        def _update() -> None:
            self._agent_start_times.pop(bead_id, None)
            self.query_one("#log", RichLog).write(
                f"[red]Failed[/red] {bead_id}: {error[:200]}"
            )

        self.call_from_thread(_update)

    def on_unmount(self) -> None:
        self.orch.shutdown()

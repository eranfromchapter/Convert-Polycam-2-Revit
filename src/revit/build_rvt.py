"""Build the existing-conditions .rvt by driving the AUTOM8LABS MCP Connector
(v3.6.2, ~278 tools) on the Revit 2027 workstation.

The recipe below ran successfully end-to-end through the connector. This
module replays it DETERMINISTICALLY as a plain MCP client — no LLM in the
loop, so no tokens per run and no drift.

⚠️ Verify on the workstation before first run: transport (REVIT_MCP_URL /
REVIT_MCP_COMMAND in .env) and the exact parameter names of each tool call
(marked VERIFY below). The tool NAMES and ORDER are the proven part.

Constraint: Revit must be OPEN on the workstation — run this from the F2
queue (scheduled task), never as an instant trigger.

Recipe:
  1. open_model      <- C:\\ProgramData\\Autodesk\\RVT 2027\\Templates\\
                        English-Imperial\\default.rte  (exact name default.rte;
                        "Imperial-Architectural Template.rte" does NOT exist)
  2. save_as         <- <workdir>\\<Slug>_Existing.rvt IMMEDIATELY (protects
                        the template)
  3. create_levels   <- Level 1 @ 0'-0", Ceiling @ height from the Polycam
                        RoomData.csv
  4. batch_link_cad  <- folder of DXFs (Polycam floor plan unchanged + the
                        scan DXF from scan_to_dxf), placeAt origin, units auto
  5. create_views    <- floor plan @ 1/4"=1'-0" + default 3D
  6. create_sheets   <- A-101 (plan), A-102 (3D), E1 30x42
  7. hide_isolate_elements <- mesh hidden in plan, plan hidden in 3D,
                        permanent:true
  8. save_as (save)  and QA screenshots via export_view_image
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import re
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

DEFAULT_CEILING_FT = 8.0


def ceiling_height_from_csv(room_csv: str | Path) -> float:
    """Pull the ceiling level height (ft) from the Polycam RoomData.csv.
    Column naming tolerant; falls back to 8'-0" with a warning."""
    heights = []
    try:
        with open(room_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            col = next((c for c in reader.fieldnames or []
                        if "ceiling" in c.lower() or "height" in c.lower()), None)
            if col:
                for row in reader:
                    m = re.search(r"[\d.]+", row.get(col) or "")
                    if m:
                        heights.append(float(m.group(0)))
    except OSError:
        pass
    if not heights:
        log.warning("no ceiling height found in %s — defaulting to %.1f ft",
                    room_csv, DEFAULT_CEILING_FT)
        return DEFAULT_CEILING_FT
    return max(heights)


class RevitJob:
    """One deal's model build. All Revit interaction goes through _call()."""

    def __init__(self, slug: str, workdir: Path, dxf_dir: Path,
                 ceiling_ft: float):
        self.slug = slug
        self.workdir = workdir
        self.dxf_dir = dxf_dir
        self.ceiling_ft = ceiling_ft
        self.rvt_path = workdir / f"{slug}_Existing.rvt"
        self.screenshots: list[Path] = []
        self._session = None

    async def _call(self, tool: str, args: dict) -> object:
        log.info("revit-mcp %s(%s)", tool, args)
        result = await self._session.call_tool(tool, args)
        if getattr(result, "isError", False):
            raise RuntimeError(f"{tool} failed: {result.content}")
        return result

    async def _run(self) -> None:
        template = settings()["f2"]["revit_template"]
        # VERIFY on workstation: parameter names below against the connector's
        # actual schemas (tool names + order are the proven recipe).
        await self._call("open_model", {"path": template})
        await self._call("save_as", {"path": str(self.rvt_path)})
        await self._call("create_levels", {"levels": [
            {"name": "Level 1", "elevation": 0.0},
            {"name": "Ceiling", "elevation": self.ceiling_ft},
        ]})
        await self._call("batch_link_cad", {
            "folder": str(self.dxf_dir), "pattern": "*.dxf",
            "placeAt": "origin", "units": "auto",
        })
        await self._call("create_views", {"views": [
            {"type": "floor_plan", "level": "Level 1",
             "scale": settings()["f2"]["plan_scale"]},
            {"type": "3d"},
        ]})
        await self._call("create_sheets", {"sheets": [
            {"number": "A-101", "name": "Existing Plan",
             "size": settings()["f2"]["sheet_size"], "views": ["floor_plan"]},
            {"number": "A-102", "name": "Existing 3D",
             "size": settings()["f2"]["sheet_size"], "views": ["3d"]},
        ]})
        # Scan mesh hidden in plan; plan linework hidden in 3D. Permanent so
        # the state survives reopening.
        await self._call("hide_isolate_elements", {
            "view": "floor_plan", "hide_pattern": f"{self.slug}_Scan*",
            "permanent": True})
        await self._call("hide_isolate_elements", {
            "view": "3d", "hide_pattern": f"{self.slug}_FloorPlan*",
            "permanent": True})
        await self._call("save_as", {"path": str(self.rvt_path)})
        for view in ("floor_plan", "3d"):
            shot = self.workdir / f"{self.slug}_QA_{view}.png"
            await self._call("export_view_image", {"view": view,
                                                   "path": str(shot)})
            self.screenshots.append(shot)

    async def _connect_and_run(self) -> None:
        from mcp import ClientSession
        transport = os.environ.get("REVIT_MCP_TRANSPORT", "sse").lower()
        if transport == "sse":
            from mcp.client.sse import sse_client
            url = os.environ["REVIT_MCP_URL"]
            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    self._session = session
                    await session.initialize()
                    await self._run()
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            cmd = os.environ["REVIT_MCP_COMMAND"].split()
            params = StdioServerParameters(command=cmd[0], args=cmd[1:])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    self._session = session
                    await session.initialize()
                    await self._run()

    def run(self) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._connect_and_run())
        if not self.rvt_path.exists():
            raise RuntimeError(f"recipe finished but {self.rvt_path} missing")
        return self.rvt_path

"""Convert a Polycam mesh export (OBJ/GLB) into a Revit-linkable 3D-face DXF.

Proven recipe (ran successfully on the workstation):
- Polycam meshes are Y-up METERS; Revit wants Z-up FEET. After Y-up→Z-up +
  m→ft the mesh co-registers with the Polycam floor-plan DXF to <1/16".
- Layers: Scan-Walls / Scan-Floors / Scan-Ceilings / Scan-Objects.
  Walls come from MTL material names (Wall_*); floors/ceilings from face
  normal + height; everything else is Objects.
- The Polycam floor-plan DXF (AutoCAD-2000, INSUNITS=2 feet, origin-centered,
  Poly-* layers) passes through UNCHANGED — do not touch it.

Usage: python -m src.revit.scan_to_dxf <mesh.glb|mesh.obj> <out.dxf>
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

M_TO_FT = 3.28083989501312  # international foot

# Y-up (Polycam) -> Z-up (Revit/AutoCAD): x'=x, y'=-z, z'=y
_YUP_TO_ZUP = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0],
    [0.0, 1.0, 0.0],
])

LAYER_WALLS = "Scan-Walls"
LAYER_FLOORS = "Scan-Floors"
LAYER_CEILINGS = "Scan-Ceilings"
LAYER_OBJECTS = "Scan-Objects"

# Face-normal classification (after Z-up transform): |nz| near 1 → horizontal.
_HORIZONTAL_NZ = 0.8
# A horizontal face in the lower band of the height range is floor; upper band
# is ceiling; middle horizontals (tables, counters) stay Objects.
_FLOOR_BAND = 0.25
_CEILING_BAND = 0.75


def convert(mesh_path: str | Path, out_dxf: str | Path) -> Path:
    import trimesh
    import ezdxf

    scene_or_mesh = trimesh.load(str(mesh_path))
    # GLB loads as a Scene of named submeshes; OBJ may too when the MTL has
    # multiple materials. Material/submesh names carry the Wall_* hint.
    if isinstance(scene_or_mesh, trimesh.Scene):
        parts = [(name, geom) for name, geom in scene_or_mesh.geometry.items()]
    else:
        parts = [(Path(mesh_path).stem, scene_or_mesh)]

    doc = ezdxf.new("AC1015")  # AutoCAD 2000, matching Polycam's own DXFs
    doc.header["$INSUNITS"] = 2  # feet
    for layer in (LAYER_WALLS, LAYER_FLOORS, LAYER_CEILINGS, LAYER_OBJECTS):
        doc.layers.add(layer)
    msp = doc.modelspace()

    # First pass: global height range (for floor/ceiling banding).
    all_z = []
    transformed = []
    for name, geom in parts:
        v = np.asarray(geom.vertices, dtype=float) @ _YUP_TO_ZUP.T * M_TO_FT
        transformed.append((name, v, np.asarray(geom.faces)))
        if len(v):
            all_z.append((v[:, 2].min(), v[:, 2].max()))
    if not all_z:
        raise ValueError(f"{mesh_path}: no geometry found")
    z_min = min(z for z, _ in all_z)
    z_max = max(z for _, z in all_z)
    z_span = max(z_max - z_min, 1e-6)

    n_faces = 0
    for name, verts, faces in transformed:
        material_is_wall = "wall" in name.lower()
        for face in faces:
            tri = verts[face]
            layer = _classify(tri, material_is_wall, z_min, z_span)
            msp.add_3dface([tuple(tri[0]), tuple(tri[1]), tuple(tri[2]),
                            tuple(tri[2])], dxfattribs={"layer": layer})
            n_faces += 1

    out_dxf = Path(out_dxf)
    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_dxf)
    log.info("wrote %s (%d 3D faces, height range %.2f–%.2f ft)",
             out_dxf, n_faces, z_min, z_max)
    return out_dxf


def _classify(tri: np.ndarray, material_is_wall: bool,
              z_min: float, z_span: float) -> str:
    if material_is_wall:
        return LAYER_WALLS
    a, b, c = tri
    n = np.cross(b - a, c - a)
    norm = np.linalg.norm(n)
    if norm < 1e-12:
        return LAYER_OBJECTS
    nz = abs(n[2] / norm)
    if nz < _HORIZONTAL_NZ:
        return LAYER_OBJECTS  # vertical-ish but not tagged Wall_* by Polycam
    rel_h = (tri[:, 2].mean() - z_min) / z_span
    if rel_h <= _FLOOR_BAND:
        return LAYER_FLOORS
    if rel_h >= _CEILING_BAND:
        return LAYER_CEILINGS
    return LAYER_OBJECTS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", help="Polycam OBJ or GLB export")
    parser.add_argument("out", help="output DXF path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    convert(args.mesh, args.out)


if __name__ == "__main__":
    main()

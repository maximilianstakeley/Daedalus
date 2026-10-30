#!/usr/bin/env python3
"""Create a patched Phase 4 copy; never edits the user's source in place."""
from __future__ import annotations
import argparse
from pathlib import Path

IMPORT_LINE = "import { createLegacyTelemetryBridge } from '/legacy-telemetry-adapter.js';"
OLD_SIG = "const sig=(n,v)=>{const s=S[n]||(S[n]={v,h:new Array(150).fill(v)});s.v=v;s.h.push(v);s.h.shift()};"
NEW_SIG = """const d4Telemetry=createLegacyTelemetryBridge({endpoint:'/api/observations',source:'daedalus-phase4-browser',intervalMs:500});
let d4PoseProvider=()=>null;
const sig=(n,v)=>{if(Number.isFinite(v))d4Telemetry.record(n,v,d4PoseProvider());const s=S[n]||(S[n]={v,h:new Array(150).fill(v)});s.v=v;s.h.push(v);s.h.shift()};"""
OLD_COLLIDER = """function bcolAdd(){if(!RW||!BG)return;if(bcol)RW.removeCollider(bcol,false);const p=BG.attributes.position.array;
 bcol=RW.createCollider(RP.ColliderDesc.trimesh(new Float32Array(p),Uint32Array.from({length:p.length/3},(_,i)=>i)))}"""
NEW_COLLIDER = """function bcolAdd(){if(!RW||!BG)return;if(bcol)RW.removeCollider(bcol,false);const p=BG.attributes.position.array;
 const ix=BG.index?new Uint32Array(BG.index.array):Uint32Array.from({length:p.length/3},(_,i)=>i);
 bcol=RW.createCollider(RP.ColliderDesc.trimesh(new Float32Array(p),ix))}"""


def patch(source: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    if IMPORT_LINE not in source:
        marker = "import * as THREE from 'three';"
        if marker not in source:
            raise ValueError("Could not locate the Three.js module import; source may have changed")
        source = source.replace(marker, marker + "\n" + IMPORT_LINE, 1)
        changes.append("added the legacy telemetry adapter import")
    if NEW_SIG not in source:
        if OLD_SIG not in source:
            raise ValueError("Could not locate the known Phase 4 sig() implementation")
        source = source.replace(OLD_SIG, NEW_SIG, 1)
        changes.append("coalesced browser signals into timestamped Daedalus API observations")
    player_marker = "const pl=new THREE.Vector3(0,0,0);let yaw=0,pitch=.35,lift=0,inv=false;"
    player_patch = "const pl=new THREE.Vector3(0,0,0);d4PoseProvider=()=>({x:pl.x,y:pl.y,z:pl.z});let yaw=0,pitch=.35,lift=0,inv=false;"
    if player_patch not in source:
        if player_marker not in source:
            raise ValueError("Could not locate the Phase 4 player-position declaration for telemetry context")
        source = source.replace(player_marker, player_patch, 1)
        changes.append("attached local player position to telemetry context without treating it as geographic data")
    if NEW_COLLIDER not in source:
        if OLD_COLLIDER not in source:
            raise ValueError("Could not locate the known bcolAdd() implementation")
        source = source.replace(OLD_COLLIDER, NEW_COLLIDER, 1)
        changes.append("used the merged mesh's actual index buffer for its physics collider")
    return source, changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to daedalussimv4.html")
    parser.add_argument("--output", type=Path, required=True, help="New output path; input is never overwritten")
    args = parser.parse_args()
    original = args.input.read_text(encoding="utf-8")
    updated, changes = patch(original)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(updated, encoding="utf-8")
    print(f"Wrote {args.output}")
    for change in changes:
        print(f"- {change}")
    if not changes:
        print("- no changes needed; source already contains these patches")


if __name__ == "__main__":
    main()

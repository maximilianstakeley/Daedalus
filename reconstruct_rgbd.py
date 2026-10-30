from __future__ import annotations

import argparse
import json
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d


def parse_timestamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"Timestamp must include timezone: {value}")
    return stamp.astimezone(timezone.utc)


def validate_pose(matrix: Any, convention: str) -> np.ndarray:
    pose = np.asarray(matrix, dtype=np.float64)
    if pose.shape != (4, 4) or not np.isfinite(pose).all():
        raise ValueError("Each camera pose must be a finite 4x4 matrix")
    if not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5):
        raise ValueError("Camera pose must be a homogeneous transform with final row [0,0,0,1]")
    rotation = pose[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-2):
        raise ValueError("Camera pose rotation block is not orthonormal; check calibration/SLAM output")
    if not np.isclose(np.linalg.det(rotation), 1.0, atol=2e-2):
        raise ValueError("Camera pose rotation must be a proper rotation (determinant approximately +1)")
    if convention == "camera_to_world":
        return np.linalg.inv(pose)  # Open3D integration expects world-to-camera extrinsics.
    if convention == "world_to_camera":
        return pose
    raise ValueError("pose_convention must be camera_to_world or world_to_camera")


def resolve_input_asset(manifest_path: Path, relative_name: str) -> Path:
    """Resolve a manifest asset while refusing absolute and parent-traversal paths."""
    if not isinstance(relative_name, str) or not relative_name:
        raise ValueError("RGB-D asset paths must be non-empty relative paths")
    root = manifest_path.parent.resolve()
    candidate = (root / relative_name).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"RGB-D asset path escapes the manifest directory: {relative_name}")
    return candidate


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    required = {"coordinate_frame", "units", "camera", "pose_convention", "frames"}
    missing = required - set(manifest)
    if missing:
        raise ValueError(f"Manifest is missing fields: {sorted(missing)}")
    if manifest["units"].get("translation") != "m" or manifest["units"].get("depth") != "m":
        raise ValueError("This worker requires camera translations and depth_scale to be expressed in metres")
    if not manifest["frames"]:
        raise ValueError("Manifest has no frames")
    return manifest


def reconstruct(manifest_path: Path, output_dir: Path, window_seconds: int = 10,
                voxel_size_m: float = 0.025, depth_trunc_m: float = 6.0) -> dict[str, Any]:
    if window_seconds < 1 or voxel_size_m <= 0 or depth_trunc_m <= 0:
        raise ValueError("window_seconds, voxel_size_m and depth_trunc_m must be positive")
    manifest = load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    camera = manifest["camera"]
    width, height = int(camera["width"]), int(camera["height"])
    fx, fy = float(camera["fx"]), float(camera["fy"])
    cx, cy = float(camera["cx"]), float(camera["cy"])
    if width < 1 or height < 1 or not all(np.isfinite(v) for v in (fx, fy, cx, cy)) or fx <= 0 or fy <= 0:
        raise ValueError("Camera dimensions must be positive and intrinsics finite with fx/fy > 0")
    intrinsic = o3d.camera.PinholeCameraIntrinsic(
        width, height, fx, fy, cx, cy
    )
    if not isinstance(manifest["frames"], list):
        raise ValueError("Manifest frames must be an array")
    frames = []
    for frame in manifest["frames"]:
        if not isinstance(frame, dict):
            raise ValueError("Each RGB-D frame manifest entry must be an object")
        timestamp = parse_timestamp(frame["timestamp"])
        extrinsic = validate_pose(frame["pose"], manifest["pose_convention"])
        # Validate both paths before any image is opened.
        resolve_input_asset(manifest_path, frame["color"])
        resolve_input_asset(manifest_path, frame["depth"])
        frames.append((timestamp, frame, extrinsic))
    frames.sort(key=lambda item: item[0])
    epoch = frames[0][0].timestamp()
    buckets: dict[int, list[tuple[datetime, dict[str, Any], np.ndarray]]] = defaultdict(list)
    for item in frames:
        bucket = int((item[0].timestamp() - epoch) // window_seconds)
        buckets[bucket].append(item)

    timeline = {
        "format": "daedalus-temporal-reconstruction-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "coordinate_frame": manifest["coordinate_frame"],
        "units": manifest["units"],
        "pose_convention_input": manifest["pose_convention"],
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_frame_count": len(frames),
        "camera": camera,
        "method": "Open3D ScalableTSDFVolume RGB-D integration",
        "window_seconds": window_seconds,
        "voxel_size_m": voxel_size_m,
        "snapshots": [],
    }

    for bucket, bucket_frames in sorted(buckets.items()):
        volume = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=voxel_size_m,
            sdf_trunc=max(4 * voxel_size_m, 0.04),
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
        )
        integrated = 0
        for timestamp, frame, world_to_camera in bucket_frames:
            color_path = resolve_input_asset(manifest_path, frame["color"])
            depth_path = resolve_input_asset(manifest_path, frame["depth"])
            if not color_path.is_file() or not depth_path.is_file():
                raise FileNotFoundError(f"Missing input frame at {timestamp.isoformat()}: {color_path} / {depth_path}")
            color = o3d.io.read_image(str(color_path))
            depth = o3d.io.read_image(str(depth_path))
            if color.is_empty() or depth.is_empty():
                raise ValueError(f"Open3D could not read RGB-D frame at {timestamp.isoformat()}")
            if color.width != width or color.height != height or depth.width != width or depth.height != height:
                raise ValueError(f"RGB-D dimensions do not match camera calibration at {timestamp.isoformat()}")
            depth_scale = float(frame.get("depth_scale", manifest.get("depth_scale", 1000.0)))
            if not np.isfinite(depth_scale) or depth_scale <= 0:
                raise ValueError(f"depth_scale must be finite and positive at {timestamp.isoformat()}")
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                color, depth,
                depth_scale=depth_scale,
                depth_trunc=depth_trunc_m,
                convert_rgb_to_intensity=False,
            )
            volume.integrate(rgbd, intrinsic, world_to_camera)
            integrated += 1

        mesh = volume.extract_triangle_mesh()
        if mesh.is_empty() or len(mesh.vertices) == 0:
            continue
        mesh.compute_vertex_normals()
        start = bucket_frames[0][0]
        end = bucket_frames[-1][0]
        filename = f"snapshot_{start.strftime('%Y%m%dT%H%M%S')}_{bucket:05d}.ply"
        mesh_path = output_dir / filename
        if not o3d.io.write_triangle_mesh(str(mesh_path), mesh, write_vertex_colors=True, write_vertex_normals=True):
            raise RuntimeError(f"Failed to write mesh: {mesh_path}")
        digest = sha256_file(mesh_path)
        snapshot = {
            "id": f"snapshot-{bucket:05d}",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "frame_count": integrated,
            "mesh": filename,
            "mesh_sha256": digest,
            "coordinate_frame": manifest["coordinate_frame"],
            "translation_units": "m",
            "vertex_count": len(mesh.vertices),
            "triangle_count": len(mesh.triangles),
            "registration_quality": manifest.get("registration_quality", "not supplied"),
            "limitations": [
                "Snapshot assumes the input camera poses register the world into one consistent frame.",
                "A new TSDF is built for each time window; this does not estimate non-rigid motion.",
                "Metric accuracy is bounded by camera calibration, depth sensor bias, pose drift and time synchronization.",
            ],
        }
        timeline["snapshots"].append(snapshot)

    out_path = output_dir / "timeline.json"
    out_path.write_text(json.dumps(timeline, indent=2))
    return timeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Build timestamped metric RGB-D mesh snapshots using Open3D TSDF fusion")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/reconstructions"))
    parser.add_argument("--window-seconds", type=int, default=10)
    parser.add_argument("--voxel-size-m", type=float, default=0.025)
    parser.add_argument("--depth-trunc-m", type=float, default=6.0)
    args = parser.parse_args()
    result = reconstruct(args.manifest, args.output, args.window_seconds, args.voxel_size_m, args.depth_trunc_m)
    print(json.dumps({"timeline": str(args.output / "timeline.json"), "snapshots": len(result["snapshots"])}, indent=2))


if __name__ == "__main__":
    main()

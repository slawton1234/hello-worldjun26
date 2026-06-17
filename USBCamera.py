#!/usr/bin/env python3
"""
Jetson USB Camera Options Inspector
Lists all available USB cameras, their V4L2 capabilities,
supported formats, resolutions, framerates, and exposes
OpenCV capture properties.
"""

import os
import subprocess
import glob
import sys

try:
    import cv2
except ImportError:
    print("[ERROR] OpenCV not found. Install with: pip3 install opencv-python")
    sys.exit(1)


# ??????????????????????????????????????????????
# 1. Enumerate video devices
# ??????????????????????????????????????????????

def list_video_devices():
    """Return a list of /dev/videoN paths that are actually cameras."""
    devices = sorted(glob.glob("/dev/video*"))
    cameras = []
    for dev in devices:
        # Only keep capture-capable devices
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--device", dev, "--all"],
                capture_output=True, text=True, timeout=3
            )
            if "Video Capture" in result.stdout:
                cameras.append(dev)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # v4l2-ctl not available — fall back to trying with OpenCV
            cap = cv2.VideoCapture(dev)
            if cap.isOpened():
                cameras.append(dev)
                cap.release()
    return cameras


# ??????????????????????????????????????????????
# 2. V4L2 capabilities via v4l2-ctl
# ??????????????????????????????????????????????

def get_v4l2_formats(device: str) -> list[dict]:
    """
    Return supported pixel formats, resolutions, and framerates
    using v4l2-ctl --list-formats-ext.
    """
    formats = []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-formats-ext"],
            capture_output=True, text=True, timeout=5
        )
        current_fmt = None
        current_res = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("["):          # e.g. [0]: 'MJPG' (Motion-JPEG)
                current_fmt = line
                formats.append({"format": current_fmt, "resolutions": []})
            elif line.startswith("Size:"):    # e.g. Size: Discrete 1920x1080
                current_res = line.split("Discrete")[-1].strip()
                if formats:
                    formats[-1]["resolutions"].append(
                        {"resolution": current_res, "framerates": []}
                    )
            elif line.startswith("Interval:") and formats and formats[-1]["resolutions"]:
                # e.g. Interval: Discrete 0.033s (30.000 fps)
                fps = line.split("(")[-1].replace("fps)", "").strip()
                formats[-1]["resolutions"][-1]["framerates"].append(fps)
    except FileNotFoundError:
        print("  [WARN] v4l2-ctl not found — skipping format enumeration.")
        print("         Install with: sudo apt install v4l-utils")
    return formats


def get_v4l2_controls(device: str) -> list[str]:
    """Return camera control options (brightness, contrast, etc.)."""
    controls = []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-ctrls"],
            capture_output=True, text=True, timeout=5
        )
        controls = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except FileNotFoundError:
        pass
    return controls


# ??????????????????????????????????????????????
# 3. OpenCV properties
# ??????????????????????????????????????????????

CV_PROPS = {
    "Width":          cv2.CAP_PROP_FRAME_WIDTH,
    "Height":         cv2.CAP_PROP_FRAME_HEIGHT,
    "FPS":            cv2.CAP_PROP_FPS,
    "Format":         cv2.CAP_PROP_FORMAT,
    "Mode":           cv2.CAP_PROP_MODE,
    "Brightness":     cv2.CAP_PROP_BRIGHTNESS,
    "Contrast":       cv2.CAP_PROP_CONTRAST,
    "Saturation":     cv2.CAP_PROP_SATURATION,
    "Hue":            cv2.CAP_PROP_HUE,
    "Gain":           cv2.CAP_PROP_GAIN,
    "Exposure":       cv2.CAP_PROP_EXPOSURE,
    "Auto Exposure":  cv2.CAP_PROP_AUTO_EXPOSURE,
    "White Balance":  cv2.CAP_PROP_WB_TEMPERATURE,
    "Auto WB":        cv2.CAP_PROP_AUTO_WB,
    "Focus":          cv2.CAP_PROP_FOCUS,
    "Auto Focus":     cv2.CAP_PROP_AUTOFOCUS,
    "Zoom":           cv2.CAP_PROP_ZOOM,
    "Buffer Size":    cv2.CAP_PROP_BUFFERSIZE,
    "Backend":        cv2.CAP_PROP_BACKEND,
}

def get_opencv_properties(device: str) -> dict:
    """Open the camera with OpenCV and read all properties."""
    props = {}
    # Try index-based (faster) then path-based
    dev_index = int(device.replace("/dev/video", ""))
    cap = cv2.VideoCapture(dev_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if cap.isOpened():
        for name, prop_id in CV_PROPS.items():
            val = cap.get(prop_id)
            if val != -1.0:   # -1 means unsupported
                props[name] = val
        cap.release()
    return props


# ??????????????????????????????????????????????
# 4. Set a camera property via OpenCV
# ??????????????????????????????????????????????

def set_camera_property(device_index: int, prop_name: str, value: float) -> bool:
    """
    Example helper: open camera and set a single property.
    Returns True on success.
    Usage:
        set_camera_property(0, "Width", 1280)
        set_camera_property(0, "Height", 720)
        set_camera_property(0, "FPS", 30)
    """
    prop_id = None
    for name, pid in CV_PROPS.items():
        if name.lower() == prop_name.lower():
            prop_id = pid
            break
    if prop_id is None:
        print(f"  [ERROR] Unknown property '{prop_name}'")
        return False
    cap = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        return False
    success = cap.set(prop_id, value)
    cap.release()
    return success


# ??????????????????????????????????????????????
# 5. GStreamer pipeline helper (Jetson-specific)
# ??????????????????????????????????????????????

def build_gstreamer_pipeline(
    device: str = "/dev/video0",
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    flip_method: int = 0
) -> str:
    """
    Build a GStreamer pipeline string for OpenCV on Jetson.
    Use with: cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    flip_method: 0=none, 1=cw90, 2=180, 3=ccw90, 4=hflip, 5=ul-diag,
                 6=vflip, 7=ur-diag
    """
    return (
        f"v4l2src device={device} ! "
        f"video/x-raw, width={width}, height={height}, framerate={fps}/1 ! "
        f"videoconvert ! "
        f"video/x-raw, format=BGR ! "
        f"videoflip method={flip_method} ! "
        f"appsink"
    )


# ??????????????????????????????????????????????
# 6. Main report
# ??????????????????????????????????????????????

def print_separator(char="?", width=60):
    print(char * width)

def main():
    print_separator("?")
    print("  Jetson USB Camera Inspector")
    print_separator("?")

    cameras = list_video_devices()

    if not cameras:
        print("\n[!] No USB camera devices found on /dev/video*")
        print("    Make sure the camera is plugged in and recognised:")
        print("    $ lsusb && ls /dev/video*")
        return

    print(f"\nFound {len(cameras)} camera device(s): {cameras}\n")

    for device in cameras:
        print_separator()
        print(f"  Device: {device}")
        print_separator()

        # --- V4L2 formats ---
        formats = get_v4l2_formats(device)
        if formats:
            print("\n  Supported Formats & Resolutions:")
            for fmt in formats:
                print(f"    {fmt['format']}")
                for res in fmt["resolutions"]:
                    fps_str = ", ".join(res["framerates"]) or "N/A"
                    print(f"      {res['resolution']}  @  {fps_str} fps")
        else:
            print("\n  [WARN] Could not retrieve V4L2 format list.")

        # --- V4L2 controls ---
        controls = get_v4l2_controls(device)
        if controls:
            print("\n  Camera Controls (v4l2):")
            for ctrl in controls:
                print(f"    {ctrl}")

        # --- OpenCV properties ---
        props = get_opencv_properties(device)
        if props:
            print("\n  OpenCV Properties:")
            for name, val in props.items():
                print(f"    {name:<18} : {val}")
        else:
            print("\n  [WARN] Could not open device with OpenCV.")

        # --- GStreamer pipeline example ---
        print("\n  GStreamer Pipeline Example (Jetson):")
        pipeline = build_gstreamer_pipeline(device)
        print(f"    {pipeline}")

        print()

    # --- Quick-start snippet ---
    print_separator("?")
    print("  Quick-Start Snippet")
    print_separator("?")
    print("""
  # Basic OpenCV capture
  import cv2
  cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
  cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
  cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
  cap.set(cv2.CAP_PROP_FPS, 30)

  while cap.isOpened():
      ret, frame = cap.read()
      if not ret:
          break
      cv2.imshow("USB Camera", frame)
      if cv2.waitKey(1) & 0xFF == ord('q'):
          break

  cap.release()
  cv2.destroyAllWindows()
""")


if __name__ == "__main__":
    main()

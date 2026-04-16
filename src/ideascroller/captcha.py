"""TikTok slider CAPTCHA detection and solving via OpenCV template matching.

Detection: checks multiple selectors (TikTok uses ByteDance's secsdk, inline DOM).
Solving: screenshots the puzzle images, uses Canny edge detection + template matching
to find the x-offset, then simulates a human-like drag with bezier easing.
"""

import asyncio
import logging
import math
import random
from typing import Callable, Optional

import cv2
import numpy as np
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# TikTok's current CAPTCHA uses TUXModal with hyphenated class names
_CAPTCHA_SELECTORS = [
    ".captcha-verify-container",          # Current (2026) — hyphens
    "#captcha-verify-container-main-page", # Current — by ID
    ".captcha_verify_container",           # Legacy — underscores
    "[id$='verify-ele']",                  # Legacy
]

# Will be discovered dynamically — these are starting guesses
_PUZZLE_BG = "#captcha-verify-image"
_PUZZLE_PIECE = ".captcha_verify_img_slide"
_SLIDER_BAR = ".captcha_verify_slide--slidebar"
_DRAG_ICON = "#captcha_slide_button"  # Current TikTok uses this ID

MAX_ATTEMPTS = 3


async def is_captcha_present(page: Page) -> bool:
    """Check if a CAPTCHA slider puzzle is visible on the page."""
    try:
        found = await page.evaluate("""() => {
            const selectors = [
                '.captcha-verify-container',
                '#captcha-verify-container-main-page',
                '.captcha_verify_container',
                '[id$="verify-ele"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return sel;
                    }
                }
            }
            // Fallback: check for "Drag the slider" text in a modal
            const modals = document.querySelectorAll('[class*="TUXModal"], [class*="captcha"]');
            for (const m of modals) {
                if (/drag.*slider|slide.*puzzle/i.test(m.textContent)) {
                    return 'text-match';
                }
            }
            return null;
        }""")
        if found:
            logger.info("CAPTCHA detected via: %s", found)
            return True
    except Exception as e:
        logger.debug("CAPTCHA check error: %s", e)
    return False


async def _wait_for_captcha_ready(page: Page, timeout: float = 8.0) -> bool:
    """Wait until CAPTCHA images are loaded inside the container."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        ready = await page.evaluate("""() => {
            const container = document.querySelector('.captcha-verify-container')
                || document.querySelector('#captcha-verify-container-main-page')
                || document.querySelector('.captcha_verify_container');
            if (!container) return false;
            // Need at least one image loaded inside
            const imgs = container.querySelectorAll('img');
            for (const img of imgs) {
                if (img.naturalWidth > 50 && img.complete) return true;
            }
            return false;
        }""")
        if ready:
            await asyncio.sleep(0.5)
            return True
        await asyncio.sleep(0.5)
    return False


async def _screenshot_element(page: Page, selector: str) -> Optional[np.ndarray]:
    """Take a screenshot of a specific element and return as OpenCV array."""
    try:
        el = await page.query_selector(selector)
        if not el:
            return None
        screenshot_bytes = await el.screenshot()
        arr = np.frombuffer(screenshot_bytes, dtype=np.uint8)
        # Load with alpha channel (IMREAD_UNCHANGED) to preserve transparency
        return cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    except Exception as e:
        logger.debug("Failed to screenshot %s: %s", selector, e)
        return None


async def _get_image_src(page: Page, selector: str) -> Optional[str]:
    """Get the src URL of an img element."""
    try:
        src = await page.evaluate(
            f"""() => {{
                const el = document.querySelector('{selector}');
                return el ? el.src : null;
            }}"""
        )
        return src
    except Exception:
        return None


async def _download_image_via_page(page: Page, url: str) -> Optional[np.ndarray]:
    """Download an image URL using the page's fetch (preserves cookies/session)."""
    try:
        img_bytes = await page.evaluate(
            """async (url) => {
                const resp = await fetch(url, { credentials: 'include' });
                if (!resp.ok) return null;
                const buf = await resp.arrayBuffer();
                return Array.from(new Uint8Array(buf));
            }""",
            url,
        )
        if not img_bytes:
            return None
        arr = np.array(img_bytes, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    except Exception as e:
        logger.debug("Failed to download image %s: %s", url, e)
        return None


async def _capture_images(
    page: Page,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Discover and capture CAPTCHA images from inside the container.

    Dynamically finds all images in the CAPTCHA container and identifies
    which is the background (larger) and which is the puzzle piece (smaller).
    """
    _log = log or (lambda msg: None)
    bg_img = None
    piece_img = None

    # Discover all images inside the captcha container
    image_info = await page.evaluate("""() => {
        const container = document.querySelector('.captcha-verify-container')
            || document.querySelector('#captcha-verify-container-main-page')
            || document.querySelector('.captcha_verify_container');
        if (!container) return null;

        const imgs = [];
        for (const img of container.querySelectorAll('img')) {
            if (!img.complete || img.naturalWidth < 10) continue;
            const rect = img.getBoundingClientRect();
            imgs.push({
                src: img.src || '',
                id: img.id || '',
                cls: (img.className || '').substring(0, 80),
                naturalW: img.naturalWidth,
                naturalH: img.naturalHeight,
                displayW: Math.round(rect.width),
                displayH: Math.round(rect.height),
                x: Math.round(rect.x),
                y: Math.round(rect.y),
            });
        }
        return imgs;
    }""")

    if not image_info:
        _log("No images found in CAPTCHA container")
        return None, None

    _log(f"Found {len(image_info)} images in CAPTCHA:")
    for img in image_info:
        _log(f"  {img['naturalW']}x{img['naturalH']} displayed={img['displayW']}x{img['displayH']} id={img['id']} cls={img['cls'][:40]}")

    # Sort by displayed size — largest is background, smallest is piece
    image_info.sort(key=lambda i: i["displayW"] * i["displayH"], reverse=True)

    if len(image_info) >= 1:
        bg_info = image_info[0]
        _log(f"BG image: {bg_info['src'][:80]}")

        if bg_info["src"]:
            bg_img = await _download_image_via_page(page, bg_info["src"])
            if bg_img is not None:
                _log(f"BG downloaded: {bg_img.shape[1]}x{bg_img.shape[0]}")

        if bg_img is None:
            # Screenshot via selector
            sel = f"#{bg_info['id']}" if bg_info["id"] else f"img[src='{bg_info['src'][:60]}']"
            bg_img = await _screenshot_element(page, sel) if bg_info["id"] else None
            if bg_img is None:
                # Screenshot the whole wrapper
                bg_img = await _screenshot_element(page, ".captcha-verify-container img")
            if bg_img is not None:
                _log(f"BG screenshot: {bg_img.shape[1]}x{bg_img.shape[0]}")

    if len(image_info) >= 2:
        piece_info = image_info[-1]  # Smallest image
        _log(f"Piece image: {piece_info['src'][:80]}")

        if piece_info["src"]:
            piece_img = await _download_image_via_page(page, piece_info["src"])
            if piece_img is not None:
                channels = piece_img.shape[2] if len(piece_img.shape) > 2 else 1
                _log(f"Piece downloaded: {piece_img.shape[1]}x{piece_img.shape[0]} ch={channels}")

        if piece_img is None:
            sel = f"#{piece_info['id']}" if piece_info["id"] else None
            if sel:
                piece_img = await _screenshot_element(page, sel)
            if piece_img is not None:
                _log(f"Piece screenshot: {piece_img.shape[1]}x{piece_img.shape[0]}")
    elif len(image_info) == 1:
        _log("Only 1 image found — might be a different CAPTCHA type")

    return bg_img, piece_img


def _to_bgr(img: np.ndarray) -> np.ndarray:
    """Convert any image to 3-channel BGR."""
    if len(img.shape) == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return img[:, :, :3]
    return img


def _create_circular_mask(size: int) -> np.ndarray:
    """Create a circular mask for comparing ring/circle images."""
    mask = np.zeros((size, size), dtype=np.uint8)
    center = size // 2
    cv2.circle(mask, (center, center), center - 2, 255, -1)
    return mask


def _extract_boundary_strip(
    img: np.ndarray, center: int, radius: int, band_width: int = 15,
) -> np.ndarray:
    """Extract a circular strip at the given radius and unroll it to a straight line.

    Like cutting the image at `radius` and flattening the ring into a rectangle.
    Result shape: (band_width, 360) — one column per degree.
    """
    gray = cv2.cvtColor(_to_bgr(img), cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    strip = np.zeros((band_width, 360), dtype=np.float32)

    for deg in range(360):
        rad = math.radians(deg)
        for d in range(band_width):
            r = radius - band_width // 2 + d
            x = int(center + r * math.cos(rad))
            y = int(center + r * math.sin(rad))
            if 0 <= x < w and 0 <= y < h:
                strip[d, deg] = gray[y, x]

    return strip


def _find_rotation_angle(
    ring_img: np.ndarray,
    piece_img: np.ndarray,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[float]:
    """Find the rotation angle by comparing the boundary where ring meets piece.

    A human solves this by looking at the SEAM — where the inner circle touches
    the outer ring. We do the same: extract a thin strip from each side of the
    boundary, unroll them into straight lines, and cross-correlate to find the
    angular offset.
    """
    _log = log or (lambda msg: None)

    ring_bgr = _to_bgr(ring_img)
    piece_bgr = _to_bgr(piece_img)
    ring_h, ring_w = ring_bgr.shape[:2]
    center = ring_w // 2
    outer_r = center - 2

    # Find inner radius (where ring content starts)
    ring_gray = cv2.cvtColor(ring_bgr, cv2.COLOR_BGR2GRAY)
    inner_r = 0
    for r in range(10, outer_r):
        sample_angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        vals = [
            ring_gray[
                int(center + r * np.sin(a)),
                int(center + r * np.cos(a)),
            ]
            for a in sample_angles
            if 0 <= int(center + r * np.cos(a)) < ring_w
            and 0 <= int(center + r * np.sin(a)) < ring_h
        ]
        if np.mean(vals) < 200:
            inner_r = max(r - 3, 0)
            break
    if inner_r < 10:
        inner_r = int(outer_r * 0.55)

    _log(f"Ring: outer_r={outer_r}, inner_r={inner_r}, center={center}")

    # Resize piece to ring size
    piece_resized = cv2.resize(piece_bgr, (ring_w, ring_h))

    # Extract boundary strips from BOTH sides of the seam
    # Ring side: just outside the hole (inner_r + a few px into the ring content)
    # Piece side: just inside the piece edge (inner_r - a few px into the piece)
    band_w = 12
    ring_strip = _extract_boundary_strip(ring_bgr, center, inner_r + band_w // 2 + 3, band_w)
    piece_strip = _extract_boundary_strip(piece_resized, center, inner_r - band_w // 2 + 1, band_w)

    # Also extract a wider band further into the ring for a second signal
    wide_band_w = 20
    ring_strip_wide = _extract_boundary_strip(
        ring_bgr, center, inner_r + wide_band_w, wide_band_w,
    )
    piece_strip_wide = _extract_boundary_strip(
        piece_resized, center, inner_r - 5, wide_band_w,
    )

    # Cross-correlate the strips to find the angular shift
    # Flatten each strip to a 1D signal (average across band width)
    ring_signal = np.mean(ring_strip, axis=0)
    piece_signal = np.mean(piece_strip, axis=0)
    ring_signal_wide = np.mean(ring_strip_wide, axis=0)
    piece_signal_wide = np.mean(piece_strip_wide, axis=0)

    # Normalize signals
    def _normalize(sig: np.ndarray) -> np.ndarray:
        s = sig - np.mean(sig)
        norm = np.linalg.norm(s)
        return s / norm if norm > 0 else s

    ring_norm = _normalize(ring_signal)
    piece_norm = _normalize(piece_signal)
    ring_wide_norm = _normalize(ring_signal_wide)
    piece_wide_norm = _normalize(piece_signal_wide)

    # Cross-correlation via FFT (circular cross-correlation)
    corr_narrow = np.fft.ifft(np.fft.fft(ring_norm) * np.conj(np.fft.fft(piece_norm))).real
    corr_wide = np.fft.ifft(np.fft.fft(ring_wide_norm) * np.conj(np.fft.fft(piece_wide_norm))).real

    # Combined score — weight both signals
    corr_combined = 0.4 * corr_narrow / (np.max(np.abs(corr_narrow)) + 1e-10) + \
                    0.6 * corr_wide / (np.max(np.abs(corr_wide)) + 1e-10)

    best_shift = int(np.argmax(corr_combined))
    best_score = corr_combined[best_shift]

    # Refine: sub-degree interpolation using parabolic fit around peak
    left = corr_combined[(best_shift - 1) % 360]
    right = corr_combined[(best_shift + 1) % 360]
    peak = corr_combined[best_shift]
    denom = 2.0 * (2 * peak - left - right)
    sub_offset = (left - right) / denom if abs(denom) > 1e-10 else 0.0
    refined_shift = best_shift + sub_offset

    _log(f"Cross-correlation: shift={refined_shift:.1f}°, score={best_score:.4f}")

    # The shift from cross-correlation is how many degrees the piece needs to
    # rotate to align with the ring. Positive shift = rotate counterclockwise
    # in our unrolled coordinate system.
    # The TikTok slider rotates CLOCKWISE when dragged right.
    # So slider angle = shift (the cross-correlation shift IS the clockwise angle
    # because we're comparing ring vs piece in the same polar direction).
    slider_angle = refined_shift % 360.0

    _log(f"Slider angle: {slider_angle:.1f}° CW")

    return slider_angle


async def _get_slider_geometry(page: Page) -> Optional[dict]:
    """Discover the slider handle and track geometry dynamically."""
    try:
        geo = await page.evaluate("""() => {
            const container = document.querySelector('.captcha-verify-container')
                || document.querySelector('#captcha-verify-container-main-page')
                || document.querySelector('.captcha_verify_container');
            if (!container) return null;

            // Find the slide button (drag handle)
            const handle = container.querySelector('#captcha_slide_button')
                || container.querySelector('.secsdk-captcha-drag-icon')
                || container.querySelector('[id*="slide_button"]');
            if (!handle) return null;

            const handleRect = handle.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();

            // Walk up from handle to find the actual slider track
            // (the widest ancestor that's still narrower than the container)
            let track = handle.parentElement;
            let trackRect = track ? track.getBoundingClientRect() : handleRect;

            // Keep walking up until we find something significantly wider than the handle
            for (let i = 0; i < 5 && track; i++) {
                const r = track.getBoundingClientRect();
                if (r.width > handleRect.width * 2) {
                    trackRect = r;
                    break;
                }
                track = track.parentElement;
                if (track) trackRect = track.getBoundingClientRect();
            }

            // If track is still too narrow, use the container width minus padding
            if (trackRect.width <= handleRect.width * 1.5) {
                trackRect = {
                    x: containerRect.x + 16,
                    width: containerRect.width - 32,
                };
            }

            return {
                handleX: handleRect.x,
                handleY: handleRect.y,
                handleWidth: handleRect.width,
                handleHeight: handleRect.height,
                trackX: trackRect.x,
                trackWidth: trackRect.width,
                containerWidth: containerRect.width,
            };
        }""")
        return geo
    except Exception as e:
        logger.debug("Failed to get slider geometry: %s", e)
        return None


def _bezier_ease_out(t: float) -> float:
    """Cubic bezier ease-out curve for natural-feeling drag."""
    # Approximation of cubic-bezier(0.25, 0.1, 0.25, 1.0)
    return 1 - (1 - t) ** 3


async def _humanized_drag(
    page: Page, start_x: float, start_y: float, offset_x: float,
) -> None:
    """Simulate a human-like drag with bezier easing, jitter, and overshoot."""
    # Move to handle and hover briefly
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.2, 0.4))

    # Press and hold
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.1, 0.25))

    # Drag with bezier easing over 800-1300ms
    total_time = random.uniform(0.8, 1.3)
    steps = random.randint(30, 50)
    step_delay = total_time / steps

    for i in range(1, steps + 1):
        progress = i / steps
        eased = _bezier_ease_out(progress)

        x = start_x + offset_x * eased
        # Small y-jitter that follows a wave pattern (more natural than pure random)
        y_jitter = math.sin(progress * math.pi * 3) * random.uniform(0.5, 1.5)
        y = start_y + y_jitter

        await page.mouse.move(x, y)
        await asyncio.sleep(step_delay + random.uniform(-0.005, 0.01))

    # Overshoot by 2-6px then ease back
    overshoot = random.uniform(2, 6)
    await page.mouse.move(start_x + offset_x + overshoot, start_y + random.uniform(-1, 1))
    await asyncio.sleep(random.uniform(0.08, 0.15))

    # Ease back to exact position
    await page.mouse.move(start_x + offset_x, start_y)
    await asyncio.sleep(random.uniform(0.15, 0.3))

    # Release
    await page.mouse.up()


async def solve_captcha(
    page: Page,
    on_log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Detect and solve a TikTok slider CAPTCHA.

    Returns True if solved (or no CAPTCHA present). Returns False only if
    all auto-solve attempts AND the manual fallback timeout both fail.
    """
    log = on_log or (lambda msg: None)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not await is_captcha_present(page):
            return True

        if attempt > 1:
            log(f"CAPTCHA retry {attempt}/{MAX_ATTEMPTS}...")
            # Wait for fresh CAPTCHA to load after failed attempt
            await asyncio.sleep(2)

        # Wait for all CAPTCHA elements to be ready
        if not await _wait_for_captcha_ready(page):
            log("CAPTCHA elements didn't load")
            continue

        # Capture images
        bg_img, piece_img = await _capture_images(page, log)

        if bg_img is None:
            log("Couldn't capture background image")
            continue
        if piece_img is None:
            log("Couldn't capture puzzle piece")
            continue

        log(f"Images: bg={bg_img.shape[1]}x{bg_img.shape[0]} piece={piece_img.shape[1]}x{piece_img.shape[0]}")

        # Save debug images to /tmp for inspection
        try:
            cv2.imwrite("/tmp/captcha_bg.png", bg_img)
            cv2.imwrite("/tmp/captcha_piece.png", piece_img)
        except Exception:
            pass

        # This is a rotation CAPTCHA — find the angle that aligns piece with ring
        raw_angle = _find_rotation_angle(bg_img, piece_img, log)
        if raw_angle is None:
            log("Couldn't find rotation angle")
            continue

        # On retry, try the inverted direction (360 - angle)
        if attempt == 2:
            angle = (360.0 - raw_angle) % 360.0
            log(f"Retry with inverted angle: {angle:.1f}°")
        elif attempt == 3:
            # Third attempt: try 180° offset from original
            angle = (raw_angle + 180.0) % 360.0
            log(f"Retry with 180° offset: {angle:.1f}°")
        else:
            angle = raw_angle

        # Get slider geometry
        geo = await _get_slider_geometry(page)
        if not geo:
            log("Couldn't read slider geometry")
            continue

        log(
            f"Geometry: track={geo['trackWidth']:.0f}px, "
            f"handle={geo['handleWidth']:.0f}x{geo['handleHeight']:.0f}px, "
            f"container={geo['containerWidth']:.0f}px"
        )

        # Convert rotation angle to slider drag distance
        # Slider goes from 0 to (track_width - handle_width)
        # Full drag = 360 degrees of rotation
        usable_track = geo["trackWidth"] - geo["handleWidth"]
        if usable_track < 20:
            # Fallback: estimate from container
            usable_track = geo["containerWidth"] - 32 - geo["handleWidth"]
            log(f"Track too narrow, using container estimate: {usable_track:.0f}px")
        drag_distance = float((angle / 360.0) * usable_track)

        # Drag handle center coordinates
        handle_cx = float(geo["handleX"] + geo["handleWidth"] / 2)
        handle_cy = float(geo["handleY"] + geo["handleHeight"] / 2)

        log(
            f"Solving: angle={angle:.1f}°, "
            f"drag={drag_distance:.0f}px / {usable_track:.0f}px, "
            f"handle=({handle_cx:.0f},{handle_cy:.0f})"
        )

        await _humanized_drag(page, handle_cx, handle_cy, drag_distance)

        # Wait for verification result
        await asyncio.sleep(2.5)
        if not await is_captcha_present(page):
            log("CAPTCHA solved!")
            return True

    # Auto-solve failed — fall back to manual
    log("CAPTCHA couldn't be solved automatically — solve it in the browser")
    for _ in range(120):
        if not await is_captcha_present(page):
            log("CAPTCHA cleared!")
            return True
        await asyncio.sleep(1)

    return False

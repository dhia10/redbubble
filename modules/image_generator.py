"""
image_generator.py  (FREE TIER EDITION)
=========================================
Generates print-ready artwork using Leonardo AI free tier.

Free tier budget: ~150 tokens/day
Token cost: ~12 tokens per 768×768 image  →  ~12 images/day safely

Pipeline:
1.  Submit generation job to Leonardo REST API
2.  Poll for completion (max 90 seconds)
3.  Download the best image to disk
4.  Upscale / pad to 4500×5400 px using Pillow (free, local)
5.  Return ImageResult with all metadata

Fallback chain (all free):
  Leonardo AI  →  Placeholder generator (solid-colour + text label)

The placeholder is used when Leonardo quota is exhausted or unreachable,
so the pipeline never hard-fails — you just upload a placeholder and
replace it manually later.
"""

from __future__ import annotations

import io
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Leonardo model IDs (free-tier compatible)
# ---------------------------------------------------------------------------

LEONARDO_MODELS: dict[str, str] = {
    "phoenix":          "b24e16ff-06e3-43eb-8d33-4416c2d75876",
    "lightning_xl":     "aa77f04e-3eec-4034-9c07-d0f619684628",
    "diffusion_xl":     "1e60896f-3c26-4296-8ecc-53e2afecc132",
    "anime_xl":         "e71a1c2f-4f80-4800-934f-2c68979d8cc8",
    "creative":         "6bef9f1b-29cb-40c7-b9df-32b51c1f67d3",
    "vision_xl":        "5c232a9e-9061-4777-980a-ddc8e65647c6",
}

DEFAULT_MODEL_ID = LEONARDO_MODELS["phoenix"]

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImageResult:
    success: bool
    file_path: str = ""
    width_px: int = 0
    height_px: int = 0
    file_size_bytes: int = 0
    format: str = "PNG"
    is_valid: bool = False
    validation_msg: str = ""
    provider: str = ""
    model: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# ImageGenerator
# ---------------------------------------------------------------------------

class ImageGenerator:
    """
    Generates AI artwork via Leonardo AI (free tier).

    Falls back to a labelled placeholder if Leonardo is unavailable.
    """

    TARGET_WIDTH  = 4500
    TARGET_HEIGHT = 5400

    def __init__(
        self,
        provider: str = "leonardo",
        output_dir: str = "data/images",
        target_width: int = TARGET_WIDTH,
        target_height: int = TARGET_HEIGHT,
        model: str = "phoenix",
        gen_width: int = 768,
        gen_height: int = 768,
        num_inference_steps: int = 25,
        guidance_scale: float = 7.0,
        openai_api_key:   Optional[str] = None,   # kept for API compat
        leonardo_api_key: Optional[str] = None,
        ideogram_api_key: Optional[str] = None,   # kept for API compat
    ) -> None:
        self.provider       = provider.lower()
        self.output_dir     = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.target_width   = target_width
        self.target_height  = target_height
        self.gen_width      = gen_width
        self.gen_height     = gen_height
        self.num_inference_steps = num_inference_steps
        self.guidance_scale = guidance_scale

        # Resolve model ID
        self.model_id = LEONARDO_MODELS.get(model, model)
        self.model    = model

        self._leo_key = (
            leonardo_api_key
            or os.environ.get("LEONARDO_API_KEY", "")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        idea_title: str = "",
        negative_prompt: str = "",
    ) -> ImageResult:
        """
        Generate one image from a prompt.

        Parameters
        ----------
        prompt          : Main image generation prompt
        idea_title      : Human-readable label (for logging)
        negative_prompt : Things to exclude from the image

        Returns
        -------
        ImageResult
        """
        logger.info("Generating image | provider=%s | title='%s'",
                    self.provider, idea_title)

        result = self._generate_leonardo(prompt, idea_title, negative_prompt)

        if not result.success:
            logger.warning(
                "Leonardo failed (%s) — using placeholder.", result.error
            )
            result = self._generate_placeholder(idea_title or prompt[:50])

        if result.success and result.file_path:
            result = self._upscale_to_print(result)

        return result

    # ------------------------------------------------------------------
    # Leonardo AI
    # ------------------------------------------------------------------

    def _generate_leonardo(
        self,
        prompt: str,
        title: str,
        negative_prompt: str,
    ) -> ImageResult:
        """Submit a generation job and poll for the result."""
        if not self._leo_key:
            return ImageResult(
                success=False,
                error="LEONARDO_API_KEY not set",
                provider="leonardo",
            )

        headers = {
            "Authorization": f"Bearer {self._leo_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

        payload: dict = {
            "prompt":     prompt[:1000],
            "modelId":    self.model_id,
            "width":      self.gen_width,
            "height":     self.gen_height,
            "num_images": 1,
            "public":     False,
            "nsfw":       False,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        try:
            with httpx.Client(timeout=60) as client:
                # ── Step 1: submit job ──────────────────────────────
                resp = client.post(
                    "https://cloud.leonardo.ai/api/rest/v1/generations",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 429:
                    return ImageResult(
                        success=False,
                        error="Leonardo daily token quota exhausted (429)",
                        provider="leonardo",
                    )
                resp.raise_for_status()

                data     = resp.json()
                gen_job  = data.get("sdGenerationJob", {})
                gen_id   = gen_job.get("generationId", "")
                if not gen_id:
                    return ImageResult(
                        success=False,
                        error=f"No generationId returned: {data}",
                        provider="leonardo",
                    )
                logger.info("Leonardo job submitted. ID: %s", gen_id)

                # ── Step 2: poll for completion ─────────────────────
                poll_url = (
                    f"https://cloud.leonardo.ai/api/rest/v1/generations/{gen_id}"
                )
                for attempt in range(18):          # Max ~90 seconds
                    time.sleep(5)
                    poll = client.get(poll_url, headers=headers)
                    poll.raise_for_status()
                    poll_data = poll.json()

                    gen_info   = poll_data.get("generations_by_pk", {})
                    status     = gen_info.get("status", "PENDING")
                    images     = gen_info.get("generated_images", [])

                    logger.debug(
                        "Poll %d/18 — status: %s, images: %d",
                        attempt + 1, status, len(images),
                    )

                    if images:
                        # Pick the first (highest-NSFW-filtered) image
                        img_url = images[0].get("url", "")
                        if img_url:
                            return self._download_image(img_url, client)

                    if status in ("FAILED", "CANCELLED"):
                        return ImageResult(
                            success=False,
                            error=f"Leonardo generation {status}",
                            provider="leonardo",
                        )

                return ImageResult(
                    success=False,
                    error="Leonardo generation timed out after 90s",
                    provider="leonardo",
                )

        except httpx.HTTPStatusError as exc:
            logger.error("Leonardo HTTP error: %s — %s",
                         exc.response.status_code, exc.response.text[:200])
            return ImageResult(
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:100]}",
                provider="leonardo",
            )
        except Exception as exc:
            logger.error("Leonardo generation exception: %s", exc)
            return ImageResult(
                success=False, error=str(exc), provider="leonardo"
            )

    def _download_image(
        self, img_url: str, client: httpx.Client
    ) -> ImageResult:
        """Download an image from URL and re-save as proper RGBA PNG."""
        filename = self._unique_filename()
        out_path = self.output_dir / filename
        try:
            img_resp = client.get(img_url, timeout=30)
            img_resp.raise_for_status()
            raw_data = img_resp.content
            # Re-open and convert to RGBA PNG (Leonardo may return JPEG)
            from PIL import Image as PILImage
            import io as _io
            with PILImage.open(_io.BytesIO(raw_data)) as _img:
                _img_rgba = _img.convert("RGBA")
                _img_rgba.save(str(out_path), format="PNG", optimize=False)
            saved_size = out_path.stat().st_size
            logger.info("Image downloaded → RGBA PNG: %s (%d bytes, orig %d bytes)",
                        out_path, saved_size, len(raw_data))
            return ImageResult(
                success=True,
                file_path=str(out_path),
                format="PNG",
                provider="leonardo",
                model=self.model,
            )
        except Exception as exc:
            return ImageResult(
                success=False,
                error=f"Download failed: {exc}",
                provider="leonardo",
            )

    # ------------------------------------------------------------------
    # Placeholder (zero-cost fallback)
    # ------------------------------------------------------------------

    def _generate_placeholder(self, label: str) -> ImageResult:
        """
        Create a coloured placeholder PNG using Pillow.
        This ensures the pipeline never fully breaks when Leonardo
        is unavailable — the slot can be manually replaced later.
        """
        try:
            from PIL import Image as PILImage, ImageDraw, ImageFont

            img = PILImage.new(
                "RGBA",
                (self.gen_width, self.gen_height),
                (180, 180, 210, 255),
            )
            draw = ImageDraw.Draw(img)
            # Draw a simple border
            draw.rectangle(
                [10, 10, self.gen_width - 10, self.gen_height - 10],
                outline=(120, 120, 180, 255),
                width=4,
            )
            # Label
            text = f"PLACEHOLDER\n{label[:40]}"
            try:
                # Try to use a built-in font
                font = ImageFont.truetype("arial.ttf", size=20)
            except Exception:
                font = ImageFont.load_default()
            draw.text((20, self.gen_height // 2 - 30), text,
                      fill=(60, 60, 100, 255), font=font)

            filename = self._unique_filename()
            out_path = self.output_dir / filename
            img.save(str(out_path), format="PNG")
            logger.info("Placeholder saved: %s", out_path)

            return ImageResult(
                success=True,
                file_path=str(out_path),
                width_px=self.gen_width,
                height_px=self.gen_height,
                format="PNG",
                is_valid=True,
                validation_msg="Placeholder — replace with real artwork",
                provider="placeholder",
                model="none",
            )
        except ImportError:
            logger.error("Pillow not installed — cannot create placeholder.")
            return ImageResult(
                success=False,
                error="Pillow unavailable for placeholder generation",
                provider="placeholder",
            )
        except Exception as exc:
            return ImageResult(
                success=False,
                error=f"Placeholder creation failed: {exc}",
                provider="placeholder",
            )

    # ------------------------------------------------------------------
    # Upscale to print size
    # ------------------------------------------------------------------

    def _upscale_to_print(self, result: ImageResult) -> ImageResult:
        """
        Upscale the generated image to TARGET_WIDTH × TARGET_HEIGHT using
        high-quality Lanczos resampling, then save as 300-DPI transparent PNG.

        Key fix: PIL's thumbnail() only SHRINKS — it never enlarges.
        We use resize() with the calculated scale factor so the artwork
        actually fills the canvas (e.g. 768px → 4500px) instead of staying
        tiny in the center of a transparent canvas.
        """
        try:
            from PIL import Image as PILImage

            img_path = Path(result.file_path)
            with PILImage.open(img_path) as img:
                orig_w, orig_h = img.size

                # Ensure RGBA (transparent background support)
                img = img.convert("RGBA")

                if orig_w == self.target_width and orig_h == self.target_height:
                    # Already correct size — just enforce 300 DPI metadata
                    img.save(
                        str(img_path), format="PNG",
                        dpi=(300, 300), optimize=False,
                    )
                    result.width_px        = orig_w
                    result.height_px       = orig_h
                    result.format          = "PNG"
                    result.is_valid        = True
                    result.validation_msg  = "OK (already at target size, DPI set)"
                    result.file_size_bytes = img_path.stat().st_size
                    return result

                # Scale UP proportionally so the artwork fills the target canvas.
                # thumbnail() only shrinks — resize() handles enlargement correctly.
                scale   = min(self.target_width / orig_w,
                              self.target_height / orig_h)
                new_w   = int(orig_w * scale)
                new_h   = int(orig_h * scale)
                scaled  = img.resize((new_w, new_h), PILImage.LANCZOS)

                # Paste onto fully-transparent canvas (correct Redbubble format)
                canvas  = PILImage.new(
                    "RGBA",
                    (self.target_width, self.target_height),
                    (0, 0, 0, 0),          # Fully transparent background
                )
                offset_x = (self.target_width  - new_w) // 2
                offset_y = (self.target_height - new_h) // 2
                canvas.paste(scaled, (offset_x, offset_y), scaled)

                # Save with 300 DPI metadata (important for Redbubble validation)
                canvas.save(
                    str(img_path), format="PNG",
                    dpi=(300, 300), optimize=False,
                )

            result.width_px        = self.target_width
            result.height_px       = self.target_height
            result.file_size_bytes = img_path.stat().st_size
            result.format          = "PNG"
            result.is_valid        = True
            result.validation_msg  = (
                f"Upscaled {orig_w}×{orig_h} → "
                f"{self.target_width}×{self.target_height} "
                f"(scale={scale:.2f}x, 300 DPI)"
            )
            size_mb = result.file_size_bytes / 1024 / 1024
            logger.info(
                "Upscaled %dx%d → %dx%d (%.2fx) @ 300 DPI | %.1f MB | %s",
                orig_w, orig_h,
                self.target_width, self.target_height,
                scale, size_mb, result.file_path,
            )

        except ImportError:
            result.is_valid       = True
            result.validation_msg = "Pillow unavailable — no resize"
        except Exception as exc:
            result.is_valid       = False
            result.validation_msg = f"Resize failed: {exc}"
            logger.error("Image upscale failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Daily token budget helper
    # ------------------------------------------------------------------

    @staticmethod
    def check_remaining_tokens(api_key: str) -> Optional[int]:
        """
        Query Leonardo API for remaining daily token balance.
        Tries multiple field names since the API response structure
        varies across account types.
        Returns None if the request fails or field not found.
        """
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    "https://cloud.leonardo.ai/api/rest/v1/me",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()

                # Log full structure once so user can see what fields exist
                logger.debug("Leonardo /me response: %s", data)

                # Try every known field name for token balance
                details = data.get("user_details", [{}])
                detail  = details[0] if details else {}
                for field in (
                    "apiCredit", "tokenBalance", "subscriptionTokens",
                    "apiCreditBalance", "credits", "remainingTokens",
                    "daily_tokens", "token_balance",
                ):
                    val = detail.get(field)
                    if val is not None:
                        logger.info("Leonardo tokens (%s): %s", field, val)
                        return int(val)

                # Also check top-level keys
                for field in ("apiCredit", "credits", "tokenBalance"):
                    val = data.get(field)
                    if val is not None:
                        return int(val)

                # Token field not found — API may not expose it on free tier
                logger.info(
                    "Leonardo token field not found in response. "
                    "Available keys in user_details: %s",
                    list(detail.keys()),
                )
        except Exception as exc:
            logger.debug("Token check failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_filename() -> str:
        return f"design_{uuid.uuid4().hex[:12]}.png"


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    gen = ImageGenerator(
        provider="leonardo",
        model="phoenix",
        gen_width=768,
        gen_height=768,
        output_dir="data/images",
    )

    # Check token balance first
    key = os.environ.get("LEONARDO_API_KEY", "")
    tokens = ImageGenerator.check_remaining_tokens(key)
    print(f"Remaining Leonardo tokens: {tokens}")

    result = gen.generate(
        prompt=(
            "A cute cartoon axolotl wearing a tiny wizard hat, floating in a "
            "magical glowing forest. Flat vector illustration, pastel colours, "
            "transparent background, sticker art style, thick black outline."
        ),
        idea_title="Axolotl Wizard",
        negative_prompt="blurry, watermark, text, realistic, photo",
    )

    print(f"\nSuccess:  {result.success}")
    print(f"Path:     {result.file_path}")
    print(f"Size:     {result.width_px} × {result.height_px}")
    print(f"Valid:    {result.is_valid}")
    print(f"Message:  {result.validation_msg}")
    if result.error:
        print(f"Error:    {result.error}")

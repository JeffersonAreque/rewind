"""SAM 3.1 on a Modal GPU: boxes of the elements of one legacy keyframe.

Imported only when REWIND_SEGMENTATION=sam (see rewind/segment.py). The image
installs torch and Meta's sam3 package on Modal; nothing heavy runs locally.
The weights come from the gated Hugging Face repo SAM_REPO (default
facebook/sam3.1) with the token in the Modal secret "huggingface" (HF_TOKEN),
and stay in a Modal volume so later runs do not download them again.

The sam3 README documents build_sam3_image_model() (SAM 3 weights) but not how
to load the SAM 3.1 checkpoints into it, so the builder's own arguments decide:
a checkpoint_path argument gets the SAM 3.1 file, otherwise SAM 3 is used. The
answer always says which repo it really ran.

`uv run modal run rewind/sam_modal.py::show_check` builds the image and checks
it on a CPU (no weights). The GPU class needs a payment method on the Modal
account and access to the gated weights.
"""

import inspect
import io
import os
from pathlib import Path

import modal

SAM_REPO = os.environ.get("SAM_REPO", "facebook/sam3.1")
FALLBACK_REPO = "facebook/sam3"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .pip_install("torch==2.10.0", "torchvision", extra_index_url="https://download.pytorch.org/whl/cu128")
    # sam3's base install misses modules it imports (einops, pycocotools...): its notebooks extra brings them;
    # pkg_resources went away in setuptools 81
    .pip_install("sam3[notebooks] @ git+https://github.com/facebookresearch/sam3.git", "huggingface_hub", "pillow",
                 "setuptools<81", "einops", "pycocotools")
    .env({"HF_HOME": "/weights"})
)
app = modal.App("rewind-sam", image=image)
weights = modal.Volume.from_name("rewind-sam-weights", create_if_missing=True)

# A separate app with no GPU: Modal refuses any app with a GPU function until the account has a payment method.
checker = modal.App("rewind-sam-check", image=image)


@checker.function(timeout=900)
def check() -> dict:
    """CPU only: the image installs sam3, and what its image builder accepts. No weights, no GPU.

    uv run modal run rewind/sam_modal.py::show_check
    """
    from sam3.model_builder import build_sam3_image_model

    return {"builder": f"build_sam3_image_model{inspect.signature(build_sam3_image_model)}"}


@checker.local_entrypoint()
def show_check() -> None:
    print(check.remote())


@app.cls(gpu="L4", secrets=[modal.Secret.from_name("huggingface")], volumes={"/weights": weights},
         timeout=900, scaledown_window=60)
class Segmenter:
    repo: str = modal.parameter(default=SAM_REPO)

    @modal.enter()
    def load(self) -> None:
        from huggingface_hub import login, snapshot_download
        from sam3.model.sam3_image_processor import Sam3Processor
        from sam3.model_builder import build_sam3_image_model

        login(token=os.environ["HF_TOKEN"])
        # Checked with show_check: build_sam3_image_model(bpe_path, device='cpu', eval_mode, checkpoint_path,
        # load_from_HF=True, ...). The device must be set or the GPU sits idle.
        accepts = inspect.signature(build_sam3_image_model).parameters
        options = {"device": "cuda"} if "device" in accepts else {}
        self.used = FALLBACK_REPO
        if self.repo != FALLBACK_REPO and "checkpoint_path" in accepts:
            folder = Path(snapshot_download(self.repo))
            checkpoint = next(p for p in sorted(folder.rglob("*")) if p.suffix in (".pt", ".pth", ".safetensors"))
            options |= {"checkpoint_path": str(checkpoint)} | ({"load_from_HF": False} if "load_from_HF" in accepts else {})
            self.used = self.repo
        model = build_sam3_image_model(**options)
        weights.commit()
        self.processor = Sam3Processor(model)

    @modal.method()
    def boxes(self, png: bytes, concepts: list[str]) -> dict:
        """{"width", "height", "model", "found": {concept: [{"box": [x0, y0, x1, y1], "score"}]}}."""
        from PIL import Image

        picture = Image.open(io.BytesIO(png)).convert("RGB")
        state = self.processor.set_image(picture)
        found = {}
        for concept in concepts:
            out = self.processor.set_text_prompt(state=state, prompt=concept)
            found[concept] = [{"box": [float(v) for v in box], "score": float(score)}
                              for box, score in zip(out["boxes"].tolist(), out["scores"].tolist())]
        return {"width": picture.width, "height": picture.height, "model": self.used, "found": found}

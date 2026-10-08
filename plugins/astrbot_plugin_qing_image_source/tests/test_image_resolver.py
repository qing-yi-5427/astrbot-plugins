import io
from pathlib import Path

import pytest
from PIL import Image

from core.image_resolver import ImageResolutionError, materialize_image


class Component:
    def __init__(self, path: Path):
        self.path = path

    async def convert_to_file_path(self):
        return str(self.path)


def make_image(path: Path, *, image_format: str = "PNG", frames: int = 1):
    images = [Image.new("RGB", (8, 8), (index * 50, 0, 0)) for index in range(frames)]
    if frames == 1:
        images[0].save(path, format=image_format)
    else:
        images[0].save(
            path,
            format=image_format,
            save_all=True,
            append_images=images[1:],
            duration=100,
            loop=0,
        )


@pytest.mark.asyncio
async def test_materialize_validates_and_hashes_png(tmp_path: Path):
    path = tmp_path / "image.png"
    make_image(path)
    result = await materialize_image(
        Component(path), max_bytes=1024 * 1024, max_pixels=1000
    )
    assert result.mime_type == "image/png"
    assert len(result.sha256) == 64
    assert result.content == path.read_bytes()


@pytest.mark.asyncio
async def test_materialize_uses_first_gif_frame(tmp_path: Path):
    path = tmp_path / "image.gif"
    make_image(path, image_format="GIF", frames=2)
    result = await materialize_image(
        Component(path), max_bytes=1024 * 1024, max_pixels=1000
    )
    assert result.mime_type == "image/jpeg"
    assert Image.open(io.BytesIO(result.content)).format == "JPEG"


@pytest.mark.asyncio
async def test_materialize_rejects_oversized_file(tmp_path: Path):
    path = tmp_path / "image.png"
    make_image(path)
    with pytest.raises(ImageResolutionError, match="限制"):
        await materialize_image(Component(path), max_bytes=1, max_pixels=1000)

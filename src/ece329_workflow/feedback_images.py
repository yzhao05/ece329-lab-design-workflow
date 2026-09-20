"""Bounded screenshot decoding; never trust a filename or supplied MIME type."""
import base64
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_FEEDBACK_BODY = 9 * 1024 * 1024
ROLES = {'problem', 'before', 'after'}


def validate_images(images, category, has_problem):
    if type(has_problem) is not bool or not isinstance(images, list) or len(images) > 3:
        raise ValueError('Invalid feedback screenshots')
    if (images or has_problem) and category != 'final_review':
        raise ValueError('Screenshots are only supported for Final review')
    result, roles = [], set()
    for item in images:
        if not isinstance(item, dict) or set(item) != {'role', 'data_url'} or not isinstance(item['role'], str) or item['role'] not in ROLES or item['role'] in roles:
            raise ValueError('Each screenshot must have a unique problem/before/after role')
        url = item['data_url']
        if not isinstance(url, str) or len(url) > MAX_IMAGE_BYTES * 4 // 3 + 256 or not url.startswith('data:image/') or ';base64,' not in url:
            raise ValueError('Invalid screenshot data or screenshot exceeds 2 MB')
        try:
            raw = base64.b64decode(url.split(';base64,', 1)[1], validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError('Screenshot exceeds 2 MB')
            with Image.open(BytesIO(raw)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF'} or source.width * source.height > 16_000_000:
                    raise ValueError('Unsupported image format or excessive image dimensions')
                source.seek(0)
                source.load()
                # Rasterize the first frame, dropping metadata and active content.
                oriented = ImageOps.exif_transpose(source).convert('RGBA')
                image = Image.new('RGB', oriented.size, 'white')
                image.paste(oriented, mask=oriented.getchannel('A'))
                image.thumbnail((2400, 2400))
                output = BytesIO(); image.save(output, format='JPEG', quality=90)
                normalized = output.getvalue()
                if len(normalized) > MAX_IMAGE_BYTES:
                    raise ValueError('Screenshot exceeds 2 MB after decoding')
                result.append({'role': item['role'], 'data_url': 'data:image/jpeg;base64,' + base64.b64encode(normalized).decode('ascii')})
                roles.add(item['role'])
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('Screenshot could not be decoded') from exc
    if has_problem and roles != ROLES:
        raise ValueError('Final review with a conversation problem requires problem, before and after screenshots')
    return result

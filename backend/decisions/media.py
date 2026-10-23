import base64
import io
import struct
import wave

from backend.errors import DomainError


def validate_media(images):
    audio = 0
    for value in images:
        try:
            data = base64.b64decode(value, validate=True)
            if len(data) > 2 * 1024**2:
                raise ValueError("Media is limited to 2 MiB per file")
            if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
                audio += 1
                with wave.open(io.BytesIO(data)) as clip:
                    if clip.getnframes() / clip.getframerate() > 30 or clip.getnchannels() > 2:
                        raise ValueError("WAV clips must be at most 30 seconds and two channels")
            elif data.startswith(b"\x89PNG\r\n\x1a\n"):
                width, height = struct.unpack(">II", data[16:24])
                if width * height > 16_000_000 or not width or not height:
                    raise ValueError("Image dimensions exceed the limit")
            elif data.startswith(b"\xff\xd8"):
                position, dimensions = 2, None
                while position + 4 <= len(data):
                    if data[position] != 255:
                        raise ValueError("Malformed JPEG")
                    marker = data[position + 1]
                    length = struct.unpack(">H", data[position + 2 : position + 4])[0]
                    if length < 2:
                        raise ValueError("Malformed JPEG")
                    if marker in {0xC0, 0xC1, 0xC2}:
                        height, width = struct.unpack(">HH", data[position + 5 : position + 9])
                        dimensions = width * height
                        break
                    position += length + 2
                if not dimensions or dimensions > 16_000_000:
                    raise ValueError("JPEG dimensions are invalid or too large")
            else:
                raise ValueError("Use PNG, JPEG, or PCM WAV media")
        except (ValueError, struct.error, wave.Error, EOFError) as error:
            raise DomainError("invalid_media", str(error)) from None
    if audio and (audio != 1 or len(images) != 1):
        raise DomainError("mixed_media", "Send one audio clip or images, not both.")

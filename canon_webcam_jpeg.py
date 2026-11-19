"""Extract complete JPEGs from a stream of camera bytes."""


def jpeg_end(data):
    """Respect JPEG segment lengths, embedded thumbnails and entropy stuffing."""
    pos = 2
    while pos < len(data):
        if data[pos] != 255:
            raise ValueError('Invalid camera JPEG marker')
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            return None
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            return pos
        if marker == 1 or 0xd0 <= marker <= 0xd8:
            continue
        if pos + 2 > len(data):
            return None
        length = int.from_bytes(data[pos:pos+2], 'big')
        if length < 2:
            raise ValueError('Invalid camera JPEG segment')
        pos += length
        if pos > len(data):
            return None
        if marker == 0xda:
            while True:
                found = data.find(b'\xff', pos)
                if found < 0 or found + 1 >= len(data):
                    return None
                following = data[found+1]
                if following == 0 or 0xd0 <= following <= 0xd7:
                    pos = found + 2
                elif following == 255:
                    pos = found + 1
                else:
                    pos = found
                    break
    return None


def take_jpegs(buffer):
    """Yield frames and keep any unfinished frame in the buffer for the next read."""
    if len(buffer) > 32 * 1024 * 1024:
        raise ValueError('Camera frame buffer exceeded 32 MiB')
    while buffer:
        start = buffer.find(b'\xff\xd8')
        if start < 0:
            buffer[:] = buffer[-1:]
            return
        del buffer[:start]
        end = jpeg_end(buffer)
        if end is None:
            return
        frame = buffer[:end]
        del buffer[:end]
        yield frame

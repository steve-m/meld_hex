# Copyright (C) 2002-2006 Stephen Kennedy <stevek@gnome.org>
# Copyright (C) 2009-2019 Kai Willadsen <kai.willadsen@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BYTES_PER_ROW = 16
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def file_is_binary(gfile):
    """Detect if a file is binary by checking for null bytes in the first 8KB."""
    if not gfile:
        return False

    try:
        info = gfile.query_info(
            Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE,
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        content_type = info.get_content_type()
        # Explicitly non-binary types
        if content_type and (
            content_type.startswith('text/')
            or content_type == 'application/xml'
            or content_type == 'application/json'
        ):
            return False
    except GLib.Error:
        pass

    try:
        stream = gfile.read(None)
        chunk = stream.read_bytes(8192, None).get_data()
        stream.close(None)
        return b'\x00' in chunk
    except GLib.Error as err:
        if err.code in (Gio.IOErrorEnum.NOT_FOUND, Gio.IOErrorEnum.NOT_MOUNTED):
            return False
        raise


def files_are_binary(gfiles):
    """Check if any file in the list is binary."""
    for gfile in gfiles:
        if file_is_binary(gfile):
            return True
    return False


def _format_hex_line(offset, data):
    """Format a single line of hex dump output.

    Returns a string like:
    00000000  48 65 6C 6C 6F 20 57 6F  72 6C 64 21 00 00 00 00  |Hello World!....|
    """
    hex_parts = []
    for i in range(BYTES_PER_ROW):
        if i < len(data):
            hex_parts.append(f'{data[i]:02X}')
        else:
            hex_parts.append('  ')
    left = ' '.join(hex_parts[:8])
    right = ' '.join(hex_parts[8:])

    ascii_repr = []
    for i in range(BYTES_PER_ROW):
        if i < len(data):
            b = data[i]
            ascii_repr.append(chr(b) if 0x20 <= b <= 0x7E else '.')
        else:
            ascii_repr.append(' ')

    return f'{offset:08X}  {left}  {right}  |{"".join(ascii_repr)}|'


def _format_hex_dump(data):
    """Convert bytes to a list of hex dump lines."""
    lines = []
    for offset in range(0, len(data), BYTES_PER_ROW):
        chunk = data[offset:offset + BYTES_PER_ROW]
        lines.append(_format_hex_line(offset, chunk))
    return lines


def _read_binary_data(gfile):
    """Read raw bytes from a Gio.File, up to MAX_FILE_SIZE."""
    try:
        stream = gfile.read(None)
        gbytes = stream.read_bytes(MAX_FILE_SIZE, None)
        stream.close(None)
        return gbytes.get_data()
    except GLib.Error as err:
        log.error(f'Error reading binary file: {err.message}')
        return b''


def byte_index_from_col(col):
    """Return byte index (0-15) within a line from column position.

    Returns None if the column is not on a data byte (e.g. offset area,
    separators, or pipe characters).

    Line format (78 chars):
    00000000  XX XX XX XX XX XX XX XX  XX XX XX XX XX XX XX XX  |................|
    cols:     0         1111111111222222222233333333334444444444555555555566666666667777777
              0123456789012345678901234567890123456789012345678901234567890123456789012345678

    Offset area: cols 0-7
    Left hex group (bytes 0-7): cols 10-33
    Right hex group (bytes 8-15): cols 35-58
    ASCII area (bytes 0-15): cols 61-76
    """
    if 10 <= col < 34:
        return min((col - 10) // 3, 7)
    elif 35 <= col < 59:
        return 8 + min((col - 35) // 3, 7)
    elif 61 <= col < 77:
        return col - 61
    return None


def hex_positions_for_byte(byte_idx):
    """Return (hex_start_col, hex_end_col, ascii_col) for byte index 0-15.

    hex_start_col/hex_end_col delimit the two-character hex pair (exclusive end).
    ascii_col is the column of the single ASCII representation character.
    """
    if byte_idx < 8:
        hex_start = 10 + byte_idx * 3
    else:
        hex_start = 35 + (byte_idx - 8) * 3
    return (hex_start, hex_start + 2, 61 + byte_idx)


def hex_address_from_cursor(line, col):
    """Compute byte address from cursor position in hex dump text."""
    byte_offset = line * BYTES_PER_ROW
    byte_idx = byte_index_from_col(col)
    if byte_idx is not None:
        byte_offset += byte_idx
    return byte_offset


def address_to_line_col(address):
    """Convert a byte address to (line, hex_start_col)."""
    line = address // BYTES_PER_ROW
    byte_idx = address % BYTES_PER_ROW
    hex_start, _, _ = hex_positions_for_byte(byte_idx)
    return (line, hex_start)


def prepare_hex_filediff(doc, gfiles):
    """Set up a FileDiff to display binary files as hex dumps.

    Creates in-memory streams with hex dump text and configures the
    FileDiff for hex display mode (hex address in status bar).
    """
    doc._hex_mode = True
    doc._hex_streams = {}

    for pane, gfile in enumerate(gfiles):
        if gfile:
            data = _read_binary_data(gfile)
            hex_lines = _format_hex_dump(data)
            hex_text = '\n'.join(hex_lines) + '\n' if hex_lines else ''
            text_bytes = hex_text.encode('utf-8')
            doc._hex_streams[pane] = Gio.MemoryInputStream.new_from_bytes(
                GLib.Bytes.new(text_bytes))

    # Disable draw-spaces on inline diff tags so that the space drawer
    # doesn't render arrows/dots over the hex dump's structural whitespace
    for buf in doc.textbuffer:
        inline_tag = buf.get_tag_table().lookup('inline')
        if inline_tag:
            inline_tag.props.draw_spaces = False

    # Change status bar format and enable hex mode input
    for sb in doc.statusbar[:len(gfiles)]:
        sb._hex_mode = True
        sb._line_column_text = "0x{line:08X}"

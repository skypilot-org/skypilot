import unittest
from unittest import mock

import requests

from sky.utils import rich_utils


class RichUtilsTest(unittest.TestCase):

    def test_decode_rich_status_with_split_utf8(self):
        """Test that decode_rich_status handles UTF-8 characters split across chunks."""
        # This is a progress bar with UTF-8 box drawing characters
        # The character █ is a multibyte UTF-8 character (0xE2 0x96 0x88)
        progress_bar = "20%|████        | 115/581 [00:12<00:41, 11.20it/s]\n"

        # Create a response object with a custom content iterator
        mock_response = mock.MagicMock(spec=requests.Response)

        # Convert to bytes and deliberately split in the middle of a multibyte character
        # The character █ is encoded as three bytes: \xe2\x96\x88
        progress_bytes = progress_bar.encode('utf-8')

        # Find position of the first █ character and split between its bytes
        pos = progress_bytes.find(b'\xe2\x96\x88')

        # Split the bytes so that \xe2 is in one chunk and \x96\x88 is in the next
        chunk1 = progress_bytes[:pos + 1]  # Include the first byte of █ (\xe2)
        chunk2 = progress_bytes[pos +
                                1:]  # Rest of the bytes including \x96\x88

        # Configure the mock to return these chunks when iterated
        mock_response.iter_content.return_value = [chunk1, chunk2]

        # The function should handle the split character correctly
        result = list(rich_utils.decode_rich_status(mock_response))

        # Verify the output contains the correctly decoded progress bar
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], progress_bar)

    def test_decode_rich_status_with_multiple_split_characters(self):
        """Test handling multiple UTF-8 characters split across several chunks."""
        # Progress bar with multiple multibyte characters
        progress_bar = "50%|█████████████████████████████░░░░░░░░░░░░░| 250/500 [01:15<01:15, 3.33it/s]\n"
        progress_bytes = progress_bar.encode('utf-8')

        # Create chunks that split at several different points
        # Find positions of several █ characters and split between their bytes
        multibyte_char = '█'.encode('utf-8')  # \xe2\x96\x88
        positions = []
        start = 0
        while True:
            pos = progress_bytes.find(multibyte_char, start)
            if pos == -1 or len(positions) >= 3:
                break
            positions.append(pos)
            start = pos + len(multibyte_char)

        # Create chunks that split at different multibyte character boundaries
        chunks = []
        last_pos = 0
        for i, pos in enumerate(positions):
            if i % 2 == 0:  # For even indices, split after first byte of multibyte char
                chunks.append(progress_bytes[last_pos:pos + 1])
                last_pos = pos + 1
            else:  # For odd indices, split normally
                chunks.append(progress_bytes[last_pos:pos])
                last_pos = pos

        # Add the final chunk
        chunks.append(progress_bytes[last_pos:])

        # Configure mock response
        mock_response = mock.MagicMock(spec=requests.Response)
        mock_response.iter_content.return_value = chunks

        # Should handle multiple split characters correctly
        result = list(rich_utils.decode_rich_status(mock_response))

        # Verify the output
        joined_result = ''.join(r for r in result if r is not None)
        self.assertEqual(joined_result, progress_bar)

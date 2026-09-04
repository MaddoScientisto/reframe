import sys
import types
import unittest

import numpy as np
from PIL import Image

picamera2 = types.ModuleType("picamera2")
picamera2.Picamera2 = object
sys.modules.setdefault("picamera2", picamera2)

import reframe


class DitheringModeTests(unittest.TestCase):
    def setUp(self):
        width, height = 96, 64
        image = Image.new("RGB", (width, height))
        pixels = []
        for y in range(height):
            for x in range(width):
                pixels.append((x * 255 // (width - 1), y * 255 // (height - 1), (x + y) * 255 // (width + height - 2)))
        image.putdata(pixels)
        self.image = image

    def test_pairwise_mode_is_deterministic_and_physical(self):
        first = reframe.ImageProcessor.apply_dithering(
            self.image,
            saturation=0.45,
            brightness_factor=1.05,
            color_factor=1.15,
            dithering_method="bayer_natural_pair",
            tone_map="percentile",
        )
        second = reframe.ImageProcessor.apply_dithering(
            self.image,
            saturation=0.45,
            brightness_factor=1.05,
            color_factor=1.15,
            dithering_method="bayer_natural_pair",
            tone_map="percentile",
        )

        self.assertTrue(np.array_equal(np.asarray(first), np.asarray(second)))
        self.assertTrue(set(np.unique(np.asarray(first)).tolist()) <= {0, 1, 2, 3, 5, 6})

    def test_strict_game_boy_mode_is_binary(self):
        output = reframe.ImageProcessor.apply_dithering(
            self.image,
            brightness_factor=1.05,
            dithering_method="gb-default",
        )

        self.assertEqual(set(np.unique(np.asarray(output)).tolist()), {0, 1})

    def test_game_boy_color_palette_combinations_are_distinct(self):
        outputs = {}
        for palette_name, shade_indices in reframe.GB_COLOR_PALETTE_COMBINATIONS.items():
            output = reframe.ImageProcessor.apply_dithering(
                self.image,
                brightness_factor=1.05,
                dithering_method="gb-default-color",
                gb_color_palette=palette_name,
            )
            values = np.asarray(output)
            outputs[palette_name] = values
            self.assertTrue(set(np.unique(values).tolist()) <= set(shade_indices))
            self.assertTrue(set(np.unique(values).tolist()) <= {0, 1, 2, 3, 5, 6})

        self.assertGreater(len({values.tobytes() for values in outputs.values()}), 1)

    def test_display_buffer_has_expected_size_and_indices(self):
        image = Image.new("RGB", reframe.DISPLAY_IMAGE_SIZE, (137, 162, 195))
        for mode in ("bayer_natural_pair", "gb-default", "gb-default-color"):
            display_buffer, output = reframe.ImageProcessor.dither_to_display_buffer(
                image,
                saturation=0.45,
                brightness_factor=1.05,
                color_factor=1.15,
                dithering_method=mode,
                tone_map="percentile",
                gb_color_palette="blue_yellow",
            )
            self.assertEqual(output.size, reframe.DISPLAY_IMAGE_SIZE)
            self.assertEqual(len(display_buffer), 120000)
            packed = np.frombuffer(display_buffer, dtype=np.uint8)
            nibbles = np.concatenate((packed >> 4, packed & 0x0F))
            self.assertTrue(set(np.unique(nibbles).tolist()) <= {0, 1, 2, 3, 5, 6})


if __name__ == "__main__":
    unittest.main()

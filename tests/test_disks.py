import io
import unittest
from unittest.mock import patch

from mutility.disks import (
    choose_partition,
    fat_label,
    format_partitions,
    partitions_from_listing,
    raw_node,
)
from mutility.cli import main


LISTING = {
    "AllDisksAndPartitions": [
        {
            "DeviceIdentifier": "disk4",
            "Content": "GUID_partition_scheme",
            "Partitions": [
                {"DeviceIdentifier": "disk4s1", "Content": "EFI", "VolumeName": "EFI", "Size": 209715200},
                {
                    "DeviceIdentifier": "disk4s2",
                    "Content": "Microsoft Basic Data",
                    "VolumeName": "USB",
                    "Size": 15189671936,
                },
            ],
        }
    ]
}

INFOS = {
    "disk4": {"MediaName": "SanDisk 3.2Gen1", "BusProtocol": "USB"},
    "disk4s1": {
        "DeviceNode": "/dev/disk4s1",
        "FilesystemType": "msdos",
        "FilesystemUserVisibleName": "MS-DOS (FAT32)",
        "VolumeName": "EFI",
        "Size": 209715200,
        "Content": "EFI",
    },
    "disk4s2": {
        "DeviceNode": "/dev/disk4s2",
        "FilesystemType": "msdos",
        "FilesystemUserVisibleName": "MS-DOS (FAT32)",
        "VolumeName": "USB",
        "Size": 15189671936,
        "Content": "Microsoft Basic Data",
    },
}


class DiskDiscoveryTest(unittest.TestCase):
    def test_lists_fat_partitions_with_data_volume_first(self):
        partitions = partitions_from_listing(LISTING, INFOS)
        self.assertEqual([item.volume_name for item in partitions], ["USB", "EFI"])
        self.assertEqual(partitions[0].device, "/dev/disk4s2")
        self.assertEqual(partitions[0].media_name, "SanDisk 3.2Gen1")
        self.assertEqual(partitions[0].fat_label, "FAT32")
        self.assertIsNone(fat_label({"FilesystemType": "exfat", "FilesystemUserVisibleName": "ExFAT"}))

    def test_raw_node_uses_the_unbuffered_device(self):
        self.assertEqual(raw_node("/dev/disk4s2"), "/dev/rdisk4s2")

    def test_single_partition_is_selected_without_a_prompt(self):
        output = io.StringIO()
        chosen = choose_partition([partitions_from_listing(LISTING, INFOS)[0]], False, input, output)
        self.assertEqual(chosen.device, "/dev/disk4s2")
        self.assertIn("Using USB (/dev/disk4s2).", output.getvalue())

    def test_multiple_partitions_require_an_explicit_choice(self):
        partitions = partitions_from_listing(LISTING, INFOS)
        output = io.StringIO()
        answers = iter(["0", "2"])
        chosen = choose_partition(partitions, True, lambda _prompt: next(answers), output)
        self.assertEqual(chosen.volume_name, "EFI")
        self.assertIn("Enter a number from 1 to 2.", output.getvalue())
        rendered = format_partitions(partitions)
        self.assertIn("/dev/disk4s2", rendered)
        self.assertLess(rendered.index("USB"), rendered.index("EFI"))

    def test_noninteractive_choice_lists_devices_and_stops(self):
        output = io.StringIO()
        chosen = choose_partition(partitions_from_listing(LISTING, INFOS), False, input, output)
        self.assertIsNone(chosen)
        self.assertIn("Pass one of these devices explicitly.", output.getvalue())


class CommandLineDiscoveryTest(unittest.TestCase):
    def test_list_option_prints_discovered_partitions(self):
        partitions = partitions_from_listing(LISTING, INFOS)
        with patch("mutility.cli.discover_fat_partitions", return_value=partitions):
            with patch("sys.stdout", new=io.StringIO()) as stdout:
                code = main(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("/dev/disk4s2", stdout.getvalue())

    def test_scripted_run_does_not_guess_between_partitions(self):
        partitions = partitions_from_listing(LISTING, INFOS)
        with patch("mutility.cli.discover_fat_partitions", return_value=partitions):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("sys.stderr", new=io.StringIO()) as stderr:
                    code = main(["-c", "pwd"])
        self.assertEqual(code, 1)
        self.assertIn("/dev/disk4s1", stderr.getvalue())

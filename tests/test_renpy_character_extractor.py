# -*- coding: utf-8 -*-

import pickle
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from renpy_character_extractor import (  # noqa: E402
    CharacterExtractionError,
    extract_character_definitions,
    extract_character_names,
)


class RenpyCharacterExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_rpa3(self, path: Path, files: dict[str, bytes], key=0x42424242):
        header_size = 34
        body = bytearray()
        index = {}
        offset = header_size
        for name, data in files.items():
            body.extend(data)
            index[name] = [(offset ^ key, len(data) ^ key, b"")]
            offset += len(data)
        compressed_index = zlib.compress(pickle.dumps(index, protocol=2))
        header = f"RPA-3.0 {offset:016x} {key:08x}\n".encode("ascii")
        self.assertEqual(len(header), header_size)
        path.write_bytes(header + body + compressed_index)

    def _write_rpa2(self, path: Path, files: dict[str, bytes]):
        header_size = 25
        body = bytearray()
        index = {}
        offset = header_size
        for name, data in files.items():
            body.extend(data)
            index[name] = [(offset, len(data))]
            offset += len(data)
        compressed_index = zlib.compress(pickle.dumps(index, protocol=2))
        header = f"RPA-2.0 {offset:016x}\n".encode("ascii")
        self.assertEqual(len(header), header_size)
        path.write_bytes(header + body + compressed_index)

    def test_extracts_static_wrapped_multiline_and_keyword_names(self):
        script = self.root / "characters.rpy"
        script.write_text(
            """
default player_name = _("Edwin")
define suffix = " Jr."
define eileen = Character(_("Eileen"), color="#fff")
define john = Character(
    "John" + suffix,
    color="#fff",
)
define jane = Character(name="Jane")
define player = Character("[player_name]")
define dynamic = DynamicCharacter("player_name")
define narrator = Character(None)
# define fake = Character("Comment")
label start:
    "Character(\\\"Dialogue text\\\")"
""",
            encoding="utf-8",
        )

        definitions = extract_character_definitions(script)

        self.assertEqual(
            [item.name for item in definitions],
            ["Eileen", "John Jr.", "Jane", "Edwin", "Edwin"],
        )
        self.assertEqual(definitions[0].variable, "eileen")
        self.assertFalse(definitions[0].dynamic)
        self.assertTrue(definitions[3].dynamic)
        self.assertTrue(definitions[4].dynamic)
        self.assertEqual(
            extract_character_names(script),
            ["Eileen", "John Jr.", "Jane", "Edwin"],
        )

    def test_keeps_unresolved_dynamic_definition_out_of_name_list(self):
        script = self.root / "dynamic.rpy"
        script.write_text(
            'define player = DynamicCharacter("unknown_name")\n',
            encoding="utf-8",
        )

        definitions = extract_character_definitions(script)

        self.assertEqual(len(definitions), 1)
        self.assertIsNone(definitions[0].name)
        self.assertEqual(definitions[0].expression, '"unknown_name"')
        self.assertEqual(extract_character_names(script), [])

    def test_reads_rpa3_sources_and_ignores_rpyc_and_translations(self):
        archive = self.root / "scripts.rpa"
        self._write_rpa3(
            archive,
            {
                "characters.rpy": b'define a = Character("Alice")\n',
                "other.rpyc": b"compiled",
                "tl/chinese/characters.rpy": 'define a = Character("爱丽丝")\n'.encode(),
            },
        )

        self.assertEqual(extract_character_names(archive), ["Alice"])
        self.assertEqual(
            extract_character_names(archive, include_translations=True),
            ["Alice", "爱丽丝"],
        )
        definition = extract_character_definitions(archive)[0]
        self.assertEqual(definition.source, "scripts.rpa::characters.rpy")

    def test_reads_rpa2_sources(self):
        archive = self.root / "legacy.rpa"
        self._write_rpa2(
            archive,
            {"script.rpy": b'define b = Character("Bob")\n'},
        )

        self.assertEqual(extract_character_names(archive), ["Bob"])

    def test_scans_game_directory_and_deduplicates_names(self):
        game_root = self.root / "ExampleGame"
        game = game_root / "game"
        game.mkdir(parents=True)
        (game / "script.rpy").write_text(
            'define a = Character("Alice")\n', encoding="utf-8"
        )
        self._write_rpa3(
            game / "scripts.rpa",
            {
                "more.rpy": b'define a2 = Character("Alice")\n'
                b'define b = Character("Bob")\n'
            },
        )

        self.assertEqual(extract_character_names(game_root), ["Alice", "Bob"])

    def test_rejects_unsupported_files_and_archives(self):
        text = self.root / "notes.txt"
        text.write_text("test", encoding="utf-8")
        bad_archive = self.root / "bad.rpa"
        bad_archive.write_bytes(b"not an archive")

        with self.assertRaises(CharacterExtractionError):
            extract_character_names(text)
        with self.assertRaises(CharacterExtractionError):
            extract_character_names(bad_archive)


if __name__ == "__main__":
    unittest.main()

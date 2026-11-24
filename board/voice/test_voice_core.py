import unittest
from voice_core import choose_command, recognized_text


class VoiceCommandsTest(unittest.TestCase):
    def test_chinese_light_commands(self):
        self.assertIs(choose_command(['开灯']), True)
        self.assertIs(choose_command(['关灯']), False)
        self.assertIs(choose_command(['开 灯']), True)
        self.assertIs(choose_command(['关闭灯']), False)

    def test_repeated_command_is_safe(self):
        self.assertIs(choose_command(['开灯', '开灯']), True)

    def test_background_speech_is_ignored(self):
        self.assertIsNone(choose_command(['[unk]']))
        self.assertIsNone(choose_command(['天气怎么样']))
        self.assertIsNone(choose_command([]))

    def test_conflicting_commands_rejected(self):
        with self.assertRaises(ValueError):
            choose_command(['开灯', '关灯'])

    def test_vosk_json_result(self):
        self.assertEqual(recognized_text('{"text":"开灯"}'), '开灯')
        self.assertEqual(recognized_text('{"text":""}'), '')
        with self.assertRaises(ValueError):
            recognized_text('{"text":42}')


if __name__ == '__main__':
    unittest.main()

"""Voice endpoint limits and cleanup without a physical audio device."""
import io
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'board'))
if sys.platform == 'win32':
    sys.modules['fcntl'] = types.ModuleType('fcntl')
import mobile_api
import voice_audio


class TestHandler(mobile_api.Handler):
    def __init__(self, data=b'', content_type='application/vnd.weiju.pcm16le'):
        self.headers = {'Content-Length': str(len(data)), 'Content-Type': content_type}
        self.rfile = io.BytesIO(data)
        self.wfile = io.BytesIO()
        self.responses = []

    def reply(self, status, data):
        self.responses.append((status, data))

    def send_response(self, status):
        self.responses.append((status, None))

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


class VoiceApiTests(unittest.TestCase):
    def test_play_rejects_wrong_format_and_excessive_length(self):
        handler = TestHandler(b'\0' * 3200, 'application/json')
        handler.play_voice()
        self.assertEqual(handler.responses[0][0], 415)
        handler = TestHandler(b'\0' * (voice_audio.MAX_PLAY_BYTES + 2))
        handler.play_voice()
        self.assertEqual(handler.responses[0][0], 400)

    def test_play_rejects_incomplete_upload(self):
        handler = TestHandler(b'\0' * 3200)
        handler.headers['Content-Length'] = '6400'
        with mock.patch.object(voice_audio, 'play_pcm') as playback:
            handler.play_voice()
        self.assertEqual(handler.responses[0][0], 400)
        playback.assert_not_called()

    def test_play_success_and_lock_release(self):
        handler = TestHandler(b'\0' * 3200)
        with mock.patch.object(voice_audio, 'play_pcm') as playback:
            handler.play_voice()
        self.assertEqual(handler.responses[0][0], 200)
        playback.assert_called_once()
        self.assertTrue(mobile_api.VOICE.acquire(False))
        mobile_api.VOICE.release()

    def test_listen_stream_and_lock_release(self):
        handler = TestHandler()
        handler.connection = object()

        def stream(headers, send):
            headers()
            send(b'\0' * 4096)

        with mock.patch.object(voice_audio, 'stream_microphone', side_effect=stream), \
                mock.patch.object(mobile_api.select, 'select', return_value=([], [], [])):
            handler.listen_voice()
        self.assertEqual(handler.responses[0][0], 200)
        self.assertEqual(len(handler.wfile.getvalue()), 4096)
        self.assertTrue(mobile_api.VOICE.acquire(False))
        mobile_api.VOICE.release()

    def test_listen_disconnect_stops_capture(self):
        handler = TestHandler()
        handler.connection = object()

        def stream(headers, send):
            headers()
            send(b'\0' * 4096)

        with mock.patch.object(voice_audio, 'stream_microphone', side_effect=stream), \
                mock.patch.object(mobile_api.select, 'select', return_value=([handler.connection], [], [])):
            handler.listen_voice()
        self.assertEqual(handler.wfile.getvalue(), b'')
        self.assertTrue(mobile_api.VOICE.acquire(False))
        mobile_api.VOICE.release()

    def test_busy_voice_device(self):
        handler = TestHandler(b'\0' * 3200)
        self.assertTrue(mobile_api.VOICE.acquire(False))
        try:
            handler.play_voice()
            self.assertEqual(handler.responses[0][0], 409)
        finally:
            mobile_api.VOICE.release()

    def test_stop_signal_requires_empty_json(self):
        handler = TestHandler(b'{"extra":true}', 'application/json')
        handler.path = '/v1/voice/stop'
        handler.authenticated = lambda: True
        handler.do_POST()
        self.assertEqual(handler.responses[0][0], 400)
        handler = TestHandler(b'{}', 'application/json')
        handler.path = '/v1/voice/stop'
        handler.authenticated = lambda: True
        handler.do_POST()
        self.assertEqual(handler.responses[0][0], 200)
        self.assertTrue(mobile_api.VOICE_STOP.is_set())
        mobile_api.VOICE_STOP.clear()


if __name__ == '__main__':
    unittest.main()

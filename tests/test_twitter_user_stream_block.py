from ..twitter_user_stream_block import TwitterUserStream
import json
from unittest.mock import MagicMock
from nio.testing.block_test_case import NIOBlockTestCase
from nio.util.discovery import not_discoverable
from threading import Event


EVENTS_MSG = {
    'event': 'EVENT_NAME'
}


DIAG_MSG = {
    'disconnect': {
        'code': 5,
        'reason': 'Normal'
    }
}


@not_discoverable
class EventTwitter(TwitterUserStream):

    def __init__(self, e):
        super().__init__()
        self._e = e

    def _notify_results(self):
        super()._notify_results()
        self._e.set()


@not_discoverable
class EventsTwitter(EventTwitter):

    def _read_line(self):
        return bytes(json.dumps(EVENTS_MSG), 'utf-8')


@not_discoverable
class DiagnosticTwitter(EventTwitter):

    def _read_line(self):
        return bytes(json.dumps(DIAG_MSG), 'utf-8')


class TestTwitterUserStream(NIOBlockTestCase):

    def setUp(self):
        super().setUp()
        self.signals = {}

        # initialize a block that won't actually talk to Twitter
        self.e = Event()
        self._block = EventsTwitter(self.e)
        self._block._connect_to_streaming = MagicMock()
        self._block._authorize = MagicMock()

    def tearDown(self):
        self._block.stop()
        super().tearDown()

    def test_events_signal(self):
        self.configure_block(self._block, {
            'name': 'TestTwitterBlock',
            'phrases': ['neutralio'],
            'notify_freq': {'milliseconds': 10}
        })
        self._block.start()
        self.e.wait(1)
        self._block._notify_results()

        notified = self.last_notified['events'][0]
        for key in EVENTS_MSG:
            self.assertEqual(getattr(notified, key), EVENTS_MSG[key])

    def test_diagnostic_message(self):
        self._block = DiagnosticTwitter(self.e)
        self._block._connect_to_streaming = MagicMock()
        self._block._authorize = MagicMock()

        self.configure_block(self._block, {
            'name': 'TestDiagnosticBlock',
            'phrases': ['neutralio'],
            'notify_freq': {'milliseconds': 10}
        })
        self._block.start()
        self.e.wait(1)
        self._block._notify_results()

        notified = self.last_notified['other'][0]
        for key in DIAG_MSG:
            self.assertEqual(getattr(notified, key), DIAG_MSG[key])

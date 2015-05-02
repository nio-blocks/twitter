from ..twitter_user_stream_block import TwitterUserStream
import json
from unittest.mock import MagicMock
from nio.util.support.block_test_case import NIOBlockTestCase
from nio.configuration.settings import Settings
from nio.modules.threading import Event


EVENTS_MSG = {
    'event': 'EVENT_NAME'
}


DIAG_MSG = {
    'disconnect': {
        'code': 5,
        'reason': 'Normal'
    }
}


class EventTwitter(TwitterUserStream):

    def __init__(self, e):
        super().__init__()
        self._e = e

    def _notify_results(self):
        super()._notify_results()
        self._e.set()


class EventsTwitter(EventTwitter):

    def _read_line(self):
        return bytes(json.dumps(EVENTS_MSG), 'utf-8')


class DiagnosticTwitter(EventTwitter):

    def _read_line(self):
        return bytes(json.dumps(DIAG_MSG), 'utf-8')


class TestTwitterUserStream(NIOBlockTestCase):

    def signals_notified(self, signals, output_id='default'):
        self.signals[output_id] = signals

    def setUp(self):
        super().setUp()
        # Settings.import_file()
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

        notified = self.signals['events'][0]
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

        notified = self.signals['other'][0]
        for key in DIAG_MSG:
            self.assertEqual(getattr(notified, key), DIAG_MSG[key])

from ..twitter_block import Twitter
import json
from unittest.mock import MagicMock
from nio.util.support.block_test_case import NIOBlockTestCase
from nio.configuration.settings import Settings
from nio.modules.threading import Event

SOME_TWEET = {
    'created_at': 'April 6, 1986',
    'text': '@World, Hello!',
    'user': {
        'name': 'societalin'
    },
    'lang': 'es'
}


class EventTwitter(Twitter):

    def __init__(self, e):
        super().__init__()
        self._e = e

    def _notify_results(self):
        super()._notify_results()
        self._e.set()

    def _read_line(self):
        self._stop_event.set()
        return bytes(json.dumps(SOME_TWEET), 'utf-8')


class TestTwitter(NIOBlockTestCase):

    def signals_notified(self, signals):
        self.signals = signals

    def setUp(self):
        super().setUp()
        # Settings.import_file()
        self.signals = None

        # initialize a block that won't actually talk to Twitter
        self.e = Event()
        self._block = EventTwitter(self.e)
        self._block._connect_to_streaming = MagicMock()
        self._block._authorize = MagicMock()

    def tearDown(self):
        self._block.stop()
        super().tearDown()

    def test_deliver_signal(self):
        self.configure_block(self._block, {
            'name': 'TestTwitterBlock',
            'phrases': ['neutralio'],
            'notify_freq': {'milliseconds': 10}
        })
        self._block.start()
        self.e.wait(1)
        self._block._notify_results()

        self.assertGreater(self._router.get_signals_from_block(self._block), 0)

        notified = self.signals[0]
        for key in SOME_TWEET:
            self.assertEqual(getattr(notified, key), SOME_TWEET[key])

    def test_select_fields(self):
        desired_fields = ['text', 'user', 'bogus']
        self.configure_block(self._block, {
            'name': 'TestTwitterBlock',
            'phrases': ['neutralio'],
            'fields': desired_fields,
            'notify_freq': {'milliseconds': 10}
        })
        self._block.start()
        self.e.wait(1)
        self._block._notify_results()

        self.assertGreater(self._router.get_signals_from_block(self._block), 0)

        notified = self.signals[0]

        # Check that all desired fields accurately came through
        for key in desired_fields:
            if key == 'bogus':
                self.assertIsNone(getattr(notified, key, None))
            else:
                self.assertEqual(getattr(notified, key), SOME_TWEET[key])

        # Check that we got ONLY those fields
        self.assertCountEqual(notified.__dict__.keys(), desired_fields[0:-1])

import json
from unittest.mock import MagicMock
from twitter.twitter_block import Twitter
from nio.util.support.block_test_case import NIOBlockTestCase
from nio.configuration.settings import Settings
from nio.modules.threading.imports import sleep

SOME_TWEET = {
    'created_at': 'April 6, 1986',
    'text': '@World, Hello!',
    'user': {
        'name': 'societalin'
    },
    'lang': 'es'
}


class DummyIterator(object):

    def iter_lines(self, chunk_size):
        return [bytes(json.dumps(SOME_TWEET), 'utf-8')]


class TestTwitter(NIOBlockTestCase):

    def signals_notified(self, signals):
        self.signals = signals

    def setUp(self):
        super().setUp()
        Settings.import_file()
        self.signals = None

        # initialize a block that won't actually talk to Twitter
        self._block = Twitter()
        self._block._connect_to_streaming = MagicMock()
        self._block._authorize = MagicMock()
        self._block._stream = DummyIterator()

    def tearDown(self):
        self._block.stop()
        super().tearDown()

    def test_deliver_signal(self):
        self.configure_block(self._block, {
            'phrases': ['neutralio']
        })
        self._block.start()
        sleep(0.2)
        self._block._notify_tweets()

        self.assert_num_signals_notified(1, self._block)
        notified = self.signals[0]
        for key in SOME_TWEET:
            self.assertEqual(getattr(notified, key), SOME_TWEET[key])

    def test_select_fields(self):
        desired_fields = ['text', 'user', 'bogus']
        self.configure_block(self._block, {
            'phrases': ['neutralio'],
            'fields': desired_fields
        })
        self._block.start()
        sleep(0.2)
        self._block._notify_tweets()

        self.assert_num_signals_notified(1, self._block)
        notified = self.signals[0]

        # Check that all desired fields accurately came through
        for key in desired_fields:
            if key == 'bogus':
                self.assertIsNone(getattr(notified, key, None))
            else:
                self.assertEqual(getattr(notified, key), SOME_TWEET[key])

        # Check that we got ONLY those fields
        self.assertCountEqual(notified.__dict__.keys(), desired_fields[0:-1])

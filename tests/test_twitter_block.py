from ..twitter_block import Twitter
import json
from unittest.mock import MagicMock
from nio.testing.block_test_case import NIOBlockTestCase
from nio.util.discovery import not_discoverable
from threading import Event


SOME_TWEET = {
    'created_at': 'April 6, 1986',
    'text': '@World, Hello!',
    'user': {
        'name': 'societalin'
    },
    'lang': 'es'
}


LIMIT_MSG = {
    'limit': {
        'track': 1234
    }
}


LIMIT_MSGS = [
    {
        'limit': {
            'track': 1234
        }
    },
    {
        'limit': {
            'track': 1000
        }
    },
    {
        'limit': {
            'track': 2000
        }
    }
]


DIAG_MSG = {
    'disconnect': {
        'code': 5,
        'reason': 'Normal'
    }
}


@not_discoverable
class EventTwitter(Twitter):

    def __init__(self, e):
        super().__init__()
        self._e = e

    def _notify_results(self):
        super()._notify_results()
        self._e.set()


@not_discoverable
class TweetTwitter(EventTwitter):

    def _read_line(self):
        self._stop_event.set()
        return bytes(json.dumps(SOME_TWEET), 'utf-8')


@not_discoverable
class LimitTwitter(EventTwitter):

    def __init__(self, e, num_events):
        super().__init__(e)
        self._read_counter = 0
        self._num_events = num_events

    def _read_line(self):
        self._read_counter += 1
        if self._read_counter >= self._num_events:
            self._stop_event.set()
        return bytes(json.dumps(LIMIT_MSGS[self._read_counter-1]), 'utf-8')


@not_discoverable
class DiagnosticTwitter(EventTwitter):

    def _read_line(self):
        self._stop_event.set()
        return bytes(json.dumps(DIAG_MSG), 'utf-8')


class TestTwitter(NIOBlockTestCase):

    def setUp(self):
        super().setUp()

        # initialize a block that won't actually talk to Twitter
        self.e = Event()
        self._block = TweetTwitter(self.e)
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

        notified = self.last_notified['tweets'][0]
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

        notified = self.last_notified['tweets'][0]

        # Check that all desired fields accurately came through
        for key in desired_fields:
            if key == 'bogus':
                self.assertIsNone(getattr(notified, key, None))
            else:
                self.assertEqual(getattr(notified, key), SOME_TWEET[key])

        # Check that we got ONLY those fields
        self.assertCountEqual(notified.__dict__.keys(), desired_fields[0:-1])

    def test_params(self):
        self.configure_block(self._block, {
            'language': ['en', 'es'],
            'locations': [{
                'southwest': {'latitude': -1.00, 'longitude': -2.00},
                'northeast': {'latitude': 1.00, 'longitude': 2.00}
            }]
        })
        self._block.start()
        params = self._block.get_params()
        self.assertEqual('none', params['filter_level'])
        self.assertEqual('en,es', params['language'])
        self.assertEqual('-2.0,-1.0,2.0,1.0', params['locations'])

    def test_limit_message(self):
        num_limits = 3
        self._block = LimitTwitter(self.e, num_limits)
        self._block._connect_to_streaming = MagicMock()
        self._block._authorize = MagicMock()

        self.configure_block(self._block, {
            'name': 'TestLimitBlock',
            'phrases': ['neutralio'],
            'notify_freq': {'milliseconds': 10}
        })
        self._block.start()
        self.e.wait(1)

        limit_counts = [1234, 0, 2000-1234]
        for i in range(num_limits):
            notified = self.last_notified['limit'][i]
            self.assertEqual(notified.cumulative_count,
                             LIMIT_MSGS[i]['limit']['track'])
            self.assertEqual(notified.limit,
                             LIMIT_MSGS[i]['limit'])
            self.assertEqual(getattr(notified, 'count'), limit_counts[i])

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

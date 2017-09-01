from nio.signal.base import Signal
from nio.block.terminals import output
from nio.properties import BoolProperty, VersionProperty

from .twitter_stream_block import TwitterStreamBlock


@output("other")
@output("events")
class TwitterUserStream(TwitterStreamBlock):

    """ A block for communicating with the User Twitter Streaming API.
    Reads user events in real time, notifying other blocks via NIO's signal
    interface at a configurable interval.

    Properties:
        notify_freq (timedelta): The interval between signal notifications.
        creds: Twitter app credentials, see above. Defaults to global settings.
        rc_interval (timedelta): Time to wait between receipts (either tweets
            or hearbeats) before attempting to reconnect to Twitter Streaming.

    """

    version = VersionProperty("2.0.0")
    only_user = BoolProperty(title="Only User Information", default=True)
    show_friends = BoolProperty(title="Include Friends List", default=False)

    streaming_host = 'userstream.twitter.com'
    streaming_endpoint = '1.1/user.json'

    def get_params(self):
        params = {
            'stall_warnings': 'true',
            'delimited': 'length'
        }

        if self.only_user:
            params['with'] = 'user'

        return params

    def get_request_method(self):
        return "GET"

    def filter_results(self, data):
        if 'friends' in data:
            if self.show_friends:
                return data
            return None

        return data

    def create_signal(self, data):
        if data and 'event' in data:
            self.logger.debug('Event message')
            with self._get_result_lock('events'):
                self._result_signals['events'].append(Signal(data))
        else:
            self.logger.debug('Other message')
            data = self.filter_results(data)
            if data:
                with self._get_result_lock('other'):
                    self._result_signals['other'].append(Signal(data))

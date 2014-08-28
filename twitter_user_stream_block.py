from .twitter_stream_block import TwitterStreamBlock
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties import BoolProperty


@Discoverable(DiscoverableType.block)
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

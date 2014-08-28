from .twitter_stream_block import TwitterStreamBlock
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties import ListProperty


@Discoverable(DiscoverableType.block)
class Twitter(TwitterStreamBlock):

    """ A block for communicating with the Twitter Streaming API.
    Reads Tweets in real time, notifying other blocks via NIO's signal
    interface at a configurable interval.

    Properties:
        phrases (list(str)): The list of phrases to track.
        fields (list(str)): Outgoing signals will pull these fields
            from incoming tweets. When empty/unset, all fields are
            included.
        notify_freq (timedelta): The interval between signal notifications.
        creds: Twitter app credentials, see above. Defaults to global settings.
        rc_interval (timedelta): Time to wait between receipts (either tweets
            or hearbeats) before attempting to reconnect to Twitter Streaming.

    """
    phrases = ListProperty(str, title='Query Phrases')
    fields = ListProperty(str, title='Included Fields')

    streaming_host = 'stream.twitter.com'
    streaming_endpoint = '1.1/statuses/filter.json'

    def get_params(self):
        return {
            'stall_warnings': 'true',
            'delimited': 'length',
            'track': ','.join(self.phrases)
        }

    def get_request_method(self):
        return "POST"

    def filter_results(self, data):
        """ Filters incoming tweet objects to include only the configured
        fields (or all of them, if self.fields is empty).

        """
        # If they did not specify which fields, just give them everything
        if not self.fields or len(self.fields) == 0:
            return data

        result = {}
        for f in self.fields:
            try:
                result[f] = data[f]
            except:
                self._logger.error("Invalid Twitter field: %s" % f)

        return result

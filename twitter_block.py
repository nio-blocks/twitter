import requests
from enum import Enum
from requests_oauthlib import OAuth1

from nio.signal.base import Signal
from nio.block.terminals import output
from nio.properties import ListProperty, SelectProperty,\
    ObjectProperty, PropertyHolder, FloatProperty, VersionProperty
from nio.types.string import StringType

from .twitter_stream_block import TwitterStreamBlock


PUB_STREAM_MSGS = {
    'limit': "Limit",
    'delete': "Deletion",
    'scrub_geo': "Location Deletion",
    'status_witheld': "Status Witheld",
    'user_witheld': "User Witheld",
    'disconnect': "Disconnect",
    'warning': "Stall Warning"
}


DISCONNECT_REASONS = [
    "Shutdown",
    "Duplicate stream",
    "Control request",
    "Stall",
    "Normal",
    "Token revoked",
    "Admin revoked",
    '',
    "Max message limit",
    "Stream exception",
    "Broker stall",
    "Shed load"
]


class FilterLevel(Enum):
    none = 0
    low = 1
    medium = 2


class Coordinate(PropertyHolder):
    latitude = FloatProperty(title='Latitude', default=0.00)
    longitude = FloatProperty(title='Longitude', default=0.00)


class Location(PropertyHolder):
    southwest = ObjectProperty(Coordinate,
                               default=Coordinate(),
                               title='Southwest')
    northeast = ObjectProperty(Coordinate,
                               default=Coordinate(),
                               title='Northeast')


@output("other")
@output("limit")
@output("tweets")
class Twitter(TwitterStreamBlock):

    """ A block for communicating with the Twitter Streaming API.
    Reads Tweets in real time, notifying other blocks via NIO's signal
    interface at a configurable interval.

    Properties:
        phrases (list(str)): The list of phrases to track.
        follow (list(str)): The list of users to track.
        fields (list(str)): Outgoing signals will pull these fields
            from incoming tweets. When empty/unset, all fields are
            included.
        language (list(str)): Only get tweets of the specifed language.
        filter_level (FilterLevel): Minimum value of the filter_level Tweet
            attribute.
        locations (list(Location)): A comma-separated list of longitude,
            latitude pairs specifying a set of bounding boxes to filter
            Tweets by.
        notify_freq (timedelta): The interval between signal notifications.
        creds: Twitter app credentials, see above. Defaults to global settings.
        rc_interval (timedelta): Time to wait between receipts (either tweets
            or hearbeats) before attempting to reconnect to Twitter Streaming.

    """

    version = VersionProperty("2.0.0")
    phrases = ListProperty(StringType, default=[], title='Query Phrases')
    follow = ListProperty(StringType, default=[], title='Follow Users')
    fields = ListProperty(StringType, default=[], title='Included Fields')
    language = ListProperty(StringType, default=['en'], title='Language')
    filter_level = SelectProperty(FilterLevel,
                                  default=FilterLevel.none,
                                  title='Filter Level')
    locations = ListProperty(Location, default=[], title='Locations')

    streaming_host = 'stream.twitter.com'
    streaming_endpoint = '1.1/statuses/filter.json'
    users_endpoint = 'https://api.twitter.com/1.1/users/lookup.json'

    def __init__(self):
        super().__init__()
        self._user_ids = []

    def _start(self):
        self._set_user_ids()

    def _set_user_ids(self):
        if len(self.follow()) == 0:
            return
        auth = OAuth1(self.creds().consumer_key(),
                      self.creds().app_secret(),
                      self.creds().oauth_token(),
                      self.creds().oauth_token_secret())
        # user ids can be grabbed 100 at a time.
        for i in range(0, len(self.follow()), 100):
            data = {"screen_name": ','.join(self.follow()[i:i+100])}
            resp = requests.post(self.users_endpoint, data=data, auth=auth)
            if resp.status_code == 200:
                for user in resp.json():
                    id = user.get('id_str')
                    if id is not None:
                        self._user_ids.append(id)
        self.logger.debug("Following {} users".format(len(self._user_ids)))

    def get_params(self):
        params = {
            'stall_warnings': 'true',
            'delimited': 'length',
            'track': ','.join(self.phrases()),
            'follow': ','.join(self._user_ids),
            'filter_level': self.filter_level().name
        }
        if self.language():
            params['language'] = ','.join(self.language())
        if self.locations():
            locations = []
            for location in self.locations():
                locations.append(str(location.southwest().longitude()))
                locations.append(str(location.southwest().latitude()))
                locations.append(str(location.northeast().longitude()))
                locations.append(str(location.northeast().latitude()))
            params['locations'] = ','.join(locations)
        return params

    def get_request_method(self):
        return "POST"

    def filter_results(self, data):
        """ Filters incoming tweet objects to include only the configured
        fields (or all of them, if self.fields is empty).

        """
        # If they did not specify which fields, just give them everything
        if not self.fields() or len(self.fields()) == 0:
            return data

        result = {}
        for f in self.fields():
            try:
                result[f] = data[f]
            except:
                self.logger.error("Invalid Twitter field: %s" % f)

        return result

    def create_signal(self, data):
        for msg in PUB_STREAM_MSGS:
            if data and msg in data:

                # Log something about the message
                report = "{} notice".format(PUB_STREAM_MSGS[msg])
                if msg == "disconnect":
                    error_idx = int(data['disconnect']['code']) - 1
                    report += ": {}".format(DISCONNECT_REASONS[error_idx])
                elif msg == "warning":
                    report += ": {}".format(data['message'])
                self.logger.debug(report)

                # Calculate total limit for limit signals
                if msg == "limit":
                    # lock when calculating limit
                    with self._get_result_lock('limit'):
                        self._calculate_limit(data)

                # Anything that is not 'limit' or 'tweet' is considered 'other'
                if msg != "limit":
                    msg = "other"

                # Add a signal to the appropriate list
                with self._get_result_lock(msg):
                    self._result_signals[msg].append(Signal(data))

                return

        # If we didn't return yet, the message is a regular tweet.
        self.logger.debug("It's a tweet!")
        data = self.filter_results(data)
        if data:
            with self._get_result_lock('tweets'):
                self._result_signals['tweets'].append(Signal(data))

    def _calculate_limit(self, data):
        """ Calculate total limit count for limit signals """
        track = data.get('limit', {}).get('track', 0)
        if track > self._limit_count:
            limit = track - self._limit_count
            self._limit_count = track
        else:
            limit = 0
        data['count'] = limit
        data['cumulative_count'] = track

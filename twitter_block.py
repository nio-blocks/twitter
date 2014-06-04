import requests
import json
from datetime import timedelta, datetime
from requests_oauthlib import OAuth1
from nio.common.block.base import Block
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties.holder import PropertyHolder
from nio.metadata.properties.list import ListProperty
from nio.metadata.properties.timedelta import TimeDeltaProperty
from nio.metadata.properties.object import ObjectProperty
from nio.metadata.properties.string import StringProperty
from nio.modules.scheduler.imports import Job
from nio.configuration.settings import Settings
from nio.common.signal.base import Signal
from nio.modules.threading.imports import Lock, spawn, Event


VERIFY_CREDS_URL = ('https://api.twitter.com/1.1/'
                    'account/verify_credentials.json')
STREAMING_URL = 'https://stream.twitter.com/1.1/statuses/filter.json'
ONE_MIN = timedelta(seconds=60)
FIVE_SEC = timedelta(seconds=5)
TCPERR_INT = timedelta(milliseconds=250)


class TwitterCreds(PropertyHolder):

    """ Property holder for Twitter OAuth credentials.

    """
    consumer_key = StringProperty(default=None)
    app_secret = StringProperty(default=None)
    oauth_token = StringProperty(default=None)
    oauth_token_secret = StringProperty(default=None)


class Tweet(Signal):

    """ A Twitter signal.

    Args:
        tweet (dict): A single tweet, JSON deserialized.

    """

    def __init__(self, tweet):
        for field in tweet:
            setattr(self, field, tweet[field])


@Discoverable(DiscoverableType.block)
class Twitter(Block):

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
    phrases = ListProperty(str)
    fields = ListProperty(str)
    notify_freq = TimeDeltaProperty(default={"minutes": 1})
    creds = ObjectProperty(TwitterCreds)
    rc_interval = TimeDeltaProperty(default={"seconds": 90})

    def __init__(self):
        super().__init__()
        self._auth = None
        self._tweets = []
        self._stop_event = Event()
        self._tweet_lock = Lock()
        self._notify_job = None
        self._stream = None
        self._monitor_job = None
        self._rc_job = None
        self._rc_delay = None
        self._last_rcv = datetime.utcnow()

    def configure(self, context):
        super().configure(context)
        self._config_default_creds()

    def start(self):
        super().start()
        self._auth = self._authorize()
        spawn(self._stream_tweets)
        self._notify_job = Job(
            self._notify_tweets,
            self.notify_freq,
            True
        )

    def stop(self):
        super().stop()
        self._stop_event.set()
        self._notify_job.cancel()
        if self._monitor_job is not None:
            self._monitor_job.cancel()
        if self._rc_job is not None:
            self._rc_job.cancel()

    def _config_default_creds(self):
        """ Grab the configured credentials, defaulting to Settings

        """
        defaults = Settings.get_entry('twitter')
        self.creds.consumer_key = self.creds.consumer_key or \
            defaults['consumer_key']
        self.creds.app_secret = self.creds.app_secret or \
            defaults['app_secret']
        self.creds.oauth_token = self.creds.oauth_token or \
            defaults['oauth_token']
        self.creds.oauth_token_secret = self.creds.oauth_token_secret or \
            defaults['oauth_token_secret']

    def _stream_tweets(self):
        """ The main thread for the Twitter block. Reads from Twitter
        streaming, parses and queues results.

        """
        if self._stream:
            self._stream.close()
            self._stream = None

        # Try to connect, if we can't, don't start streaming
        if not self._connect_to_streaming():
            return

        while(1):
            try:
                for tweet in self._stream.iter_lines(chunk_size=1):

                    # reset the last received timestamp
                    self._last_rcv = datetime.utcnow()

                    if self._stop_event.is_set():
                        break
                    elif len(tweet) > 0:
                        self._record_tweet(tweet)

            except Exception as e:

                # if there was an error while streaming, log it and re-enter
                # the iter_lines loop
                self._logger.error("While streaming tweets: %s" % str(e))
                continue

            # otherwise break out of the whole thing
            break

    def _record_tweet(self, tweet):
        """ Decode the tweet and add it to the end of the list

        """
        try:
            data = json.loads(tweet.decode('utf-8'))
            data = self._select_fields(data)
            tw = Tweet(data)
            self._tweet_lock.acquire()
            self._tweets.append(tw)
            self._tweet_lock.release()
        except Exception as e:
            self._logger.error("Could not parse Tweet: "
                               "%s" % str(e))

    def _select_fields(self, data):
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

    def _connect_to_streaming(self):
        """ Attempt to connect to the Twitter Streaming API

        Will return True if the connection is made. If a connection fails, this
        method will return False and schedule reconnection attempts using the
        backoff strategy as per Twitter's API documentation.

        """
        try:
            self._stream = requests.post(STREAMING_URL,
                                         data={'track': self._join_phrases()},
                                         headers={'User-Agent': 'NIO 1.0'},
                                         stream=True,
                                         auth=self._auth)
            status = self._stream.status_code
            self._logger.info("Streaming connection status: %d" % status)

            # reconnect attempt and backoff implementation for HTTP errors
            if status == 200:
                self._last_rcv = datetime.utcnow()
                self._rc_delay = None

                if self._rc_job is not None:
                    self._logger.debug("We were reconnecting, now we're done!")
                    self._rc_job.cancel()

                self._monitor_job = Job(
                    self._monitor_connection,
                    self.rc_interval,
                    True
                )

                # Return true, we are connected!
                return True
            elif status == 420:
                self._logger.debug("Twitter is rate limiting your requests")
                self._rc_delay = self._rc_delay or ONE_MIN / 2.0
            else:
                self._logger.error("Status %s, going to need to reconnect"
                                   % status)
                self._rc_delay = self._rc_delay or FIVE_SEC / 2.0

            # We didn't connect, double the reconnect delay
            self._rc_delay *= 2
            self._logger.debug("Doubling reconnect delay from %s" %
                               self._rc_delay)

        # handles backoff for TCP level errors
        except Exception as e:
            self._logger.error("While reconnecting to Twitter: %s" % str(e))
            self._rc_delay = self._rc_delay or timedelta(0)
            self._rc_delay += TCPERR_INT

        finally:
            if self._rc_delay is not None:
                self._logger.debug("Trying to reconnect in %s seconds" %
                                   self._rc_delay)
                self._rc_job = Job(self._stream_tweets,
                                   self._rc_delay, False)

        # If we are here, we didn't connect and we have a reconnect job
        # scheduled
        return False

    def _notify_tweets(self):
        self._tweet_lock.acquire()
        self.notify_signals(self._tweets)
        self._tweets = []
        self._tweet_lock.release()

    def _monitor_connection(self):
        """ Scheduled to run every self.rc_interval. Makes sure that some
        data has been received in the last self.rc_interval.

        """
        current_time = datetime.utcnow()
        time_since_data = current_time - self._last_rcv
        if time_since_data > self.rc_interval:
            self._logger.warning("No data received, we might be disconnected")
            self._monitor_job.cancel()
            self._stream_tweets()

    def _authorize(self):
        """ Prepare the OAuth handshake and verify.

        """
        auth = OAuth1(self.creds.consumer_key,
                      self.creds.app_secret,
                      self.creds.oauth_token,
                      self.creds.oauth_token_secret)
        if requests.get(VERIFY_CREDS_URL, auth=auth).status_code != 200:
            self._logger.error("Authentication Failed"
                               "for consumer key: %s" %
                               self.creds.consumer_key)
        return auth

    def _join_phrases(self):
        return ','.join(self.phrases)

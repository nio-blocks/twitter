import requests
import json
import oauth2 as oauth
import http.client
from datetime import timedelta, datetime
from ssl import SSLError
import time
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
from nio.modules.threading.imports import Lock, spawn, Event, sleep


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
class TwitterHybrid(Block):

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
    notify_freq = TimeDeltaProperty(default={"seconds": 2})
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
        pass
        #defaults = Settings.get_entry('twitter')
        # self.creds.consumer_key = self.creds.consumer_key or \
            # defaults['consumer_key']
        # self.creds.app_secret = self.creds.app_secret or \
            # defaults['app_secret']
        # self.creds.oauth_token = self.creds.oauth_token or \
            # defaults['oauth_token']
        # self.creds.oauth_token_secret = self.creds.oauth_token_secret or \
            # defaults['oauth_token_secret']

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
                tweet = self._getNextTweet(self._stream)
                if not tweet:
                    continue

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

    def _getNextTweet(self, resp):
        receivedStr = ''
        d = None

        while d != '\r':
            d, length = self._readFromStream(resp, 1)
            if d is None or length == 0:
                # if we claim to have read but it has length 0, it means we've
                # been disconnected
                resp.close()
                return
            else:
                receivedStr += d

        try:
            length = int(receivedStr)
        except Exception as e:
            self._logger.debug(
                "Error converting length from entry: {0}, details:"
                "{1}".format(receivedStr, str(e)))
            return

        if length:
            receivedStr, new_length = self._readFromStream(resp, length)
            if receivedStr is None or new_length == 0:
                # if we claim to have read but it has length 0, it means we've
                # been disconnected
                resp.close()
                return
            # do not hold reading functionality, save it and let scheduler
            # process it.
            return receivedStr

    # Simple Function: Read if we can, return it, otherwise return None
    def _readFromStream(self, resp, count):
        read_count = 0
        tweet_buffer = ''
        while read_count < count:
            try:
                temp_buffer = resp.read(count - read_count).decode('utf-8')
                if temp_buffer is not None:
                    length = len(temp_buffer)
                    if length == 0:
                        # requested read returned 0, this is a closed
                        # connection symptom
                        return None, 0
                    # partial read was ok, add it to current results
                    tweet_buffer += temp_buffer
                    read_count += length
                else:
                    return None, 0
            except SSLError:
                # the read timed out, nothing was read
                return None, 0
            except Exception as e:
                self._logger.error('Error reading from stream : {0}'.format(e))
                return None, 0
        return tweet_buffer, read_count

    def _connect_to_streaming(self):
        try:
            self._conn = http.client.HTTPSConnection(
                host='stream.twitter.com',
                timeout=45)

            request_params = {
                # include twitter query parameters below
                'stall_warnings': 'true',
                'delimited': 'length'
            }

            request_params['track'] = ','.join(self.phrases)

            req_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': '*/*'
            }

            # get the signed request with the proper oauth creds
            req = self._getOauthRequest(request_params)

            self._conn.request('POST',
                               STREAMING_URL,
                               body=req.to_postdata(),
                               headers=req_headers)
            response = self._conn.getresponse()

            if response.status != 200:
                self._logger.warning(
                    'Status:{0} returned from twitter'.format(response.status))
                return False
            else:
                self._logger.debug('Connected to Streaming API Successfully')

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

                self._stream = response
                # Return true, we are connected!
                return True

        except Exception as e:
            self._logger.error('Error opening connection : {0}'.format(e))
            return False

    # This function uses the oauthCreds passed from the transducer to sign the
    # request
    def _getOauthRequest(self, request_params):
        request_params['oauth_version'] = '1.0'
        request_params['oauth_nonce'] = oauth.generate_nonce()
        request_params['oauth_timestamp'] = int(time.time())

        req = oauth.Request(method='POST',
                            url=STREAMING_URL,
                            parameters=request_params)

        req.sign_request(
            signature_method=oauth.SignatureMethod_HMAC_SHA1(),
            consumer=oauth.Consumer(
                self.creds.consumer_key, self.creds.app_secret),
            token=oauth.Token(
                self.creds.oauth_token, self.creds.oauth_token_secret)
        )

        return req

    def _record_tweet(self, tweet):
        """ Decode the tweet and add it to the end of the list

        """
        try:
            data = json.loads(tweet)
            data = self._select_fields(data)
            tw = Tweet(data)
            self._tweet_lock.acquire()
            self._tweets.append(tw)
            self._tweet_lock.release()
        except Exception as e:
            return
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
